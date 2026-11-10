"""
Local runtime readiness, Docker container inspection, and cache-first loading helpers.

All checks inspect local host state (Docker images/containers, binaries, local files,
and Hugging Face cache on disk) with short timeouts and zero outbound network polling.

Examples / Doctest:
    >>> from llm_api.diagnostics import DockerDaemonStatus, DockerServiceReadiness, HuggingFaceCacheStatus
    >>> s = DockerDaemonStatus(available=False, detail="Docker daemon unreachable")
    >>> s.available
    False
    >>> hf = HuggingFaceCacheStatus(model_id="google/gemma-4-12B-it", cached_on_disk=False, cache_path=None, detail="Not cached in HF_HOME")
    >>> hf.cached_on_disk
    False
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


def load_hf_with_cache_first(
    loader_fn: Callable[..., Any],
    *args: Any,
    allow_remote: bool = True,
    **kwargs: Any,
) -> Any:
    """
    Executes a Hugging Face or SentenceTransformer loader with ``local_files_only=True`` first.
    Falls back to remote fetch only when ``allow_remote=True`` and the local snapshot is absent.
    """
    local_kwargs = {**kwargs, "local_files_only": True}
    try:
        return loader_fn(*args, **local_kwargs)
    except Exception as local_err:
        if not allow_remote:
            raise
        target = args[0] if args else kwargs.get("model_name_or_path", "resource")
        logger.info(f"Local cache miss for '{target}' ({local_err}); attempting remote/mirror fetch...")
        return loader_fn(*args, **kwargs)


@dataclass(frozen=True)
class DockerDaemonStatus:
    """Availability of the local Docker Engine socket for this process."""
    available: bool
    detail: str


@dataclass(frozen=True)
class DockerServiceReadiness:
    """Local image and container readiness for a docker-compose service."""
    service_name: str
    image_name: str
    container_name: str
    docker_accessible: bool
    image_present: bool
    container_exists: bool
    container_running: bool
    container_state: str
    detail: str


@dataclass(frozen=True)
class HuggingFaceCacheStatus:
    """Disk cache status of a Hugging Face model or local weight directory."""
    model_id: str
    cached_on_disk: bool
    cache_path: str | None
    detail: str


@dataclass(frozen=True)
class BackendAvailabilitySummary:
    """Consolidated host readiness snapshot across all local AI hosting options."""
    docker_daemon: DockerDaemonStatus
    vllm: DockerServiceReadiness
    ollama: DockerServiceReadiness
    llamacpp_binary_exists: bool
    llamacpp_binary_path: str
    llamacpp_gguf_files: tuple[str, ...]
    llamacpp_online: bool
    pytorch_cuda_available: bool
    pytorch_online: bool
    pytorch_error: str | None

    def is_backend_available(self, backend: str) -> tuple[bool, str]:
        """Returns (is_ready, concise_reason) for a SystemConfiguration HostingBackend value."""
        if backend == "pytorch":
            if self.pytorch_error:
                return False, self.pytorch_error
            if self.pytorch_online:
                hw = "CUDA" if self.pytorch_cuda_available else "CPU"
                return True, f"Inference service online ({hw})"
            hw = "CUDA available" if self.pytorch_cuda_available else "CPU only"
            return True, f"Inference daemon unstarted ({hw})"

        if backend == "vllm":
            if not self.vllm.docker_accessible:
                return False, self.vllm.detail
            if self.vllm.container_running or self.vllm.image_present:
                return True, self.vllm.detail
            return False, self.vllm.detail

        if backend == "ollama":
            if not self.ollama.docker_accessible:
                return False, self.ollama.detail
            if self.ollama.container_running or self.ollama.image_present:
                return True, self.ollama.detail
            return False, self.ollama.detail

        if backend == "llamacpp":
            if self.llamacpp_online:
                return True, f"llama-server online ({len(self.llamacpp_gguf_files)} GGUF file(s))"
            if not self.llamacpp_binary_exists:
                return False, f"Unavailable: llama-server binary not found ({self.llamacpp_binary_path})"
            if not self.llamacpp_gguf_files:
                return False, "No .gguf files found in models directory"
            return True, f"llama-server found; {len(self.llamacpp_gguf_files)} GGUF file(s) ready"

        return False, f"Unknown backend '{backend}'"


def check_docker_daemon(timeout: float = 1.5) -> DockerDaemonStatus:
    """Checks whether the local Docker CLI and daemon socket are accessible."""
    try:
        res = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if res.returncode == 0:
            version = (res.stdout or "").strip() or "unknown"
            return DockerDaemonStatus(available=True, detail=f"Docker Engine v{version}")
        err = (res.stderr or res.stdout or "").strip()
        lowered = err.lower()
        if "permission denied" in lowered:
            return DockerDaemonStatus(
                available=False,
                detail="Docker socket permission denied (add runtime user to 'docker' group)",
            )
        return DockerDaemonStatus(available=False, detail=f"Docker unavailable: {err or 'daemon stopped'}")
    except FileNotFoundError:
        return DockerDaemonStatus(available=False, detail="Docker CLI not installed")
    except subprocess.TimeoutExpired:
        return DockerDaemonStatus(available=False, detail="Docker daemon timed out")
    except OSError as exc:
        return DockerDaemonStatus(available=False, detail=f"Docker check error: {exc}")


def run_docker_compose_action(
    compose_args: list[str],
    action_label: str,
    env: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Runs a docker compose command locally and returns ``(ok, detail_message)``."""
    from django.conf import settings

    compose_file = str(settings.BASE_DIR / "docker-compose.yml")
    cmd = ["docker", "compose", "-f", compose_file, *compose_args]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)
        return True, f"{action_label} succeeded."
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        logger.error(f"❌ {action_label} failed: {err}")
        return False, err
    except FileNotFoundError as exc:
        err = f"Docker CLI not installed: {exc}"
        logger.error(f"❌ {action_label} failed: {err}")
        return False, err


def inspect_docker_image_local(image_name: str, timeout: float = 1.5) -> tuple[bool, str]:
    """Checks purely on local disk whether a Docker image tag exists."""
    try:
        res = subprocess.run(
            ["docker", "image", "inspect", image_name, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if res.returncode == 0:
            return True, (res.stdout or "").strip()[:19]
        return False, (res.stderr or res.stdout or "No such image").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)


def inspect_docker_container(container_name: str, timeout: float = 1.5) -> tuple[bool, bool, str]:
    """Checks local Docker container existence and running state without network calls."""
    try:
        res = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^/{container_name}$", "--format", "{{.State}}|{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if res.returncode != 0:
            return False, False, ""
        line = (res.stdout or "").strip().splitlines()[0] if (res.stdout or "").strip() else ""
        if not line:
            return False, False, "not created"
        parts = line.split("|", 1)
        state = parts[0].strip().lower()
        status_text = parts[1].strip() if len(parts) > 1 else state
        return True, (state == "running"), status_text
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, False, ""


def inspect_docker_service_readiness(
    service_name: str,
    image_env_var: str,
    default_image: str,
    container_name: str,
    daemon_status: DockerDaemonStatus | None = None,
) -> DockerServiceReadiness:
    """Evaluates local image and container availability for vLLM or Ollama."""
    image_name = (os.environ.get(image_env_var) or "").strip() or default_image
    daemon = daemon_status or check_docker_daemon()

    if not daemon.available:
        return DockerServiceReadiness(
            service_name=service_name,
            image_name=image_name,
            container_name=container_name,
            docker_accessible=False,
            image_present=False,
            container_exists=False,
            container_running=False,
            container_state="unreachable",
            detail=f"Unavailable: {daemon.detail}",
        )

    image_present, _ = inspect_docker_image_local(image_name)
    container_exists, container_running, container_state = inspect_docker_container(container_name)

    if container_running:
        detail = f"Container '{container_name}' running ({container_state})"
    elif image_present:
        detail = f"Image '{image_name}' cached locally (container stopped)"
    else:
        detail = f"Unavailable: Docker image '{image_name}' not found locally"

    return DockerServiceReadiness(
        service_name=service_name,
        image_name=image_name,
        container_name=container_name,
        docker_accessible=True,
        image_present=image_present,
        container_exists=container_exists,
        container_running=container_running,
        container_state=container_state,
        detail=detail,
    )


def check_hf_model_cached(model_id_or_path: str) -> HuggingFaceCacheStatus:
    """Checks purely on local disk whether a Hugging Face model snapshot is cached."""
    target = (model_id_or_path or "").strip()
    if not target:
        return HuggingFaceCacheStatus(
            model_id="",
            cached_on_disk=False,
            cache_path=None,
            detail="No model configured",
        )

    if os.path.isdir(target):
        return HuggingFaceCacheStatus(
            model_id=target,
            cached_on_disk=True,
            cache_path=target,
            detail=f"Local directory present ({target})",
        )

    try:
        from huggingface_hub import try_to_load_from_cache

        cfg_path = try_to_load_from_cache(target, "config.json")
        if isinstance(cfg_path, str) and os.path.isfile(cfg_path):
            snap_dir = os.path.dirname(cfg_path)
            weight_exts = (".safetensors", ".bin", ".pt", ".gguf")
            has_weights = any(fname.endswith(weight_exts) for fname in os.listdir(snap_dir))
            if has_weights:
                return HuggingFaceCacheStatus(
                    model_id=target,
                    cached_on_disk=True,
                    cache_path=snap_dir,
                    detail=f"Cached in HF_HOME ({os.path.basename(snap_dir)[:12]})",
                )
            return HuggingFaceCacheStatus(
                model_id=target,
                cached_on_disk=False,
                cache_path=snap_dir,
                detail="Partial cache (config.json only; model weights missing)",
            )
        return HuggingFaceCacheStatus(
            model_id=target,
            cached_on_disk=False,
            cache_path=None,
            detail="Not cached in HF_HOME",
        )
    except Exception as exc:
        return HuggingFaceCacheStatus(
            model_id=target,
            cached_on_disk=False,
            cache_path=None,
            detail=f"Cache check failed: {exc}",
        )


def check_llamacpp_binary() -> tuple[bool, str]:
    """Checks whether the bare-metal llama-server binary exists."""
    from django.conf import settings

    bin_dir = getattr(settings, "REASON_BIN_DIR", None) or os.path.join(
        os.environ.get("REASON_BASE_DIR", "/srv/reason"), "bin"
    )
    candidate = os.path.join(bin_dir, "llama-server")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return True, candidate
    on_path = shutil.which("llama-server")
    if on_path:
        return True, on_path
    return False, candidate


def get_backend_availability_summary(sys_config: Any = None) -> BackendAvailabilitySummary:
    """Builds a local-only readiness snapshot across all 4 inference backends."""
    from .llamacpp_client import is_llamacpp_available_cached
    from .models import discover_gguf_files
    from metacognition.context_processors import _PRIMARY_HEALTH_CACHE, is_primary_backend_available_cached

    daemon = check_docker_daemon()
    vllm_ready = inspect_docker_service_readiness(
        "vllm", "DOCKER_VLLM_IMAGE", "vllm/vllm-openai:latest", "verbal_vllm", daemon_status=daemon
    )
    ollama_ready = inspect_docker_service_readiness(
        "ollama", "DOCKER_OLLAMA_IMAGE", "ollama/ollama", "verbal_ollama", daemon_status=daemon
    )

    binary_ok, binary_path = check_llamacpp_binary()
    gguf_files = tuple(discover_gguf_files())
    llama_online = is_llamacpp_available_cached(ttl_seconds=1.0, timeout=0.3)

    cuda_ok = False
    try:
        import torch

        cuda_ok = bool(torch.cuda.is_available())
    except Exception:
        cuda_ok = False

    pytorch_online = False
    pytorch_err = None
    if sys_config and getattr(sys_config, "hosting_backend", None) == "pytorch":
        pytorch_online = is_primary_backend_available_cached(sys_config, ttl_seconds=1.0, timeout=0.4)
        cached_err = _PRIMARY_HEALTH_CACHE.get("pytorch_error")
        if cached_err:
            pytorch_err = cached_err[1]

    return BackendAvailabilitySummary(
        docker_daemon=daemon,
        vllm=vllm_ready,
        ollama=ollama_ready,
        llamacpp_binary_exists=binary_ok,
        llamacpp_binary_path=binary_path,
        llamacpp_gguf_files=gguf_files,
        llamacpp_online=llama_online,
        pytorch_cuda_available=cuda_ok,
        pytorch_online=pytorch_online,
        pytorch_error=pytorch_err,
    )
