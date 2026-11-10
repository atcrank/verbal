import logging
import os
from typing import Any

from .diagnostics import run_docker_compose_action
from .hardware import get_backend_recommendations

logger = logging.getLogger(__name__)


def start_container(model_target: Any, pull_policy: str = "never") -> tuple[bool, str]:
    """
    Starts or re-creates the vLLM Docker container using docker compose,
    incorporating hardware recommendations and model-specific tuning.

    Uses ``--no-build --pull never`` by default so runtime restarts never
    hang or fail pulling container images from blocked registries.

    Args:
        model_target: Either a LocalAIModel instance or a string HuggingFace model ID.
        pull_policy: Docker Compose pull policy (default: ``"never"``).
    """
    if not model_target:
        msg = "No model or model ID provided for vLLM container startup."
        logger.warning(msg)
        return False, msg

    model_id = getattr(model_target, "hf_model_id", None) or str(model_target)

    # Attempt to resolve model object if a string was passed
    model_obj = None
    if hasattr(model_target, "hf_model_id"):
        model_obj = model_target
    else:
        try:
            from .models import LocalAIModel
            model_obj = LocalAIModel.objects.filter(hf_model_id=model_id).first()
        except Exception:
            pass

    # Retrieve hardware-based advisor recommendations
    context_window = getattr(model_obj, "context_window", 4096) if model_obj else 4096
    rec = get_backend_recommendations(requested_context=context_window)

    # 1. GPU Memory Utilization
    util = getattr(model_obj, "vllm_gpu_memory_utilization", None)
    if util is None or util <= 0:
        util = rec.vllm_gpu_memory_utilization

    # 2. Max Sequence Length
    max_len = getattr(model_obj, "vllm_max_model_len", None)
    if max_len is None or max_len <= 0:
        max_len = min(context_window, rec.vllm_max_model_len)

    # 3. Precision / Dtype
    dtype_override = getattr(model_obj, "compute_dtype", "auto") if model_obj else "auto"
    vllm_dtype = dtype_override if dtype_override != "auto" else rec.vllm_dtype

    # 4. Extra flags (LoRA + Quantization)
    extra_flags = ["--enable-lora"]
    quant_mode = getattr(model_obj, "quantization_mode", None) if model_obj else None
    if quant_mode in ["awq", "gptq", "fp8", "bitsandbytes"]:
        extra_flags.append(f"--quantization {quant_mode}")

    extra_args = " ".join(extra_flags)

    logger.info(
        f"🚀 Launching vLLM container: model={model_id}, "
        f"utilization={util}, max_len={max_len}, dtype={vllm_dtype}, args={extra_args}"
    )
    env = os.environ.copy()
    env["VLLM_MODEL"] = model_id
    env["VLLM_GPU_MEMORY_UTILIZATION"] = str(util)
    env["VLLM_MAX_MODEL_LEN"] = str(max_len)
    env["VLLM_DTYPE"] = str(vllm_dtype)
    env["VLLM_EXTRA_ARGS"] = extra_args

    args = ["up", "-d", "--no-build"]
    if pull_policy:
        args.extend(["--pull", pull_policy])
    args.extend(["--force-recreate", "vllm"])

    ok, detail = run_docker_compose_action(args, f"Start vLLM container for '{model_id}'", env=env)
    if ok:
        logger.info(f"✅ vLLM container started successfully for model '{model_id}'.")
    return ok, detail


def stop_container() -> tuple[bool, str]:
    """Stops the vLLM Docker container using docker compose."""
    logger.info("Stopping vLLM container via docker compose...")
    ok, detail = run_docker_compose_action(["stop", "vllm"], "Stop vLLM container")
    if ok:
        logger.info("✅ vLLM container stopped.")
    return ok, detail
