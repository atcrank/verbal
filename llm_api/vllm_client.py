import logging
import subprocess
import os
from typing import Any

from django.conf import settings
from .hardware import get_backend_recommendations

logger = logging.getLogger(__name__)


def start_container(model_target: Any):
    """
    Starts or re-creates the vLLM Docker container using docker compose,
    incorporating hardware recommendations and model-specific tuning.

    Args:
        model_target: Either a LocalAIModel instance or a string HuggingFace model ID.
    """
    if not model_target:
        logger.warning("No model or model ID provided for vLLM container startup.")
        return

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

    try:
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

        compose_file = str(settings.BASE_DIR / "docker-compose.yml")
        subprocess.run(
            ["docker", "compose", "-f", compose_file, "up", "-d", "--force-recreate", "vllm"],
            check=True, capture_output=True, text=True, env=env
        )
        logger.info(f"✅ vLLM container started successfully for model '{model_id}'.")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Failed to start vLLM container: {e.stderr or e.stdout or e}")


def stop_container():
    """Stops the vLLM Docker container using docker compose."""
    try:
        logger.info("Stopping vLLM container via docker compose...")
        compose_file = str(settings.BASE_DIR / "docker-compose.yml")
        subprocess.run(
            ["docker", "compose", "-f", compose_file, "stop", "vllm"],
            check=True, capture_output=True, text=True
        )
        logger.info("✅ vLLM container stopped.")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Failed to stop vLLM container: {e.stderr or e.stdout or e}")
