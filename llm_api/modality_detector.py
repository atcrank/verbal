"""
Modality Detector & Model Introspection Service.

Provides zero-VRAM, zero-weight inspection of model architectures to determine
whether an active model supports visual (multimodal / VLM) reasoning or is strictly text-based.
Prevents 'model mayhem' (VRAM OOMs, container thrashing, and tokenizer crashes)
by ensuring image payloads are only routed to models with verified vision towers.
"""

import logging
import re
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Known Vision-Language Model types in Hugging Face transformers
KNOWN_VLM_MODEL_TYPES = {
    "qwen2_vl",
    "paligemma",
    "llava",
    "llava_next",
    "mllama",
    "chameleon",
    "idefics",
    "idefics2",
    "pixtral",
    "clip",
    "blip",
    "blip-2",
    "git",
    "florence2",
    "vision-encoder-decoder",
}

# Known Vision-Language Model architectures in Hugging Face
KNOWN_VLM_ARCHITECTURES = {
    "Qwen2VLForConditionalGeneration",
    "PaliGemmaForConditionalGeneration",
    "LlavaForConditionalGeneration",
    "LlavaNextForConditionalGeneration",
    "MllamaForConditionalGeneration",
    "Idefics2ForConditionalGeneration",
    "ChameleonForConditionalGeneration",
    "VisionEncoderDecoderModel",
}

# Known external multimodal model names
KNOWN_EXTERNAL_VISION_PATTERNS = [
    r"gpt-4o",
    r"gpt-4-turbo",
    r"gpt-4-vision",
    r"claude-3",
    r"gemini-1\.5",
    r"gemini-2",
    r"qwen.*vl",
]


# In-memory cache to prevent repetitive disk I/O or network checks
_MODALITY_CACHE: Dict[str, bool] = {}


def introspect_hf_modality(model_id_or_path: str) -> bool:
    """
    Introspects a local directory or cached Hugging Face model repository to determine
    if it is a multimodal / vision-language model.
    
    PREFERS LOCAL DISK:
    1. First attempts purely offline load (`local_files_only=True`) from local disk/HF cache.
    2. Zero GPU VRAM, zero network polling when model is present locally.
    3. Caches result in memory to avoid repetitive disk I/O.
    """
    if not model_id_or_path:
        return False

    if model_id_or_path in _MODALITY_CACHE:
        return _MODALITY_CACHE[model_id_or_path]

    name_lower = model_id_or_path.lower()

    try:
        from transformers import AutoConfig

        # Priority 1: Check local disk / cache ONLY (no network polling)
        try:
            cfg = AutoConfig.from_pretrained(model_id_or_path, local_files_only=True, trust_remote_code=True)
        except Exception:
            # Priority 2: If not in local cache, fallback to standard lookup
            cfg = AutoConfig.from_pretrained(model_id_or_path, trust_remote_code=True)

        is_multi = False
        # 1. Check for explicit vision_config attribute
        if hasattr(cfg, "vision_config") and cfg.vision_config is not None:
            is_multi = True
        # 2. Check model_type
        elif getattr(cfg, "model_type", "").lower() in KNOWN_VLM_MODEL_TYPES:
            is_multi = True
        # 3. Check declared architectures
        elif any(arch in KNOWN_VLM_ARCHITECTURES for arch in (getattr(cfg, "architectures", []) or [])):
            is_multi = True
        else:
            model_type = getattr(cfg, "model_type", "").lower()
            archs = " ".join(getattr(cfg, "architectures", []) or []).lower()
            if "vision" in model_type or "vl" in model_type or "vision" in archs or "vl" in archs:
                is_multi = True

        _MODALITY_CACHE[model_id_or_path] = is_multi
        return is_multi
    except Exception as e:
        logger.debug(f"HF modality introspection skipped for '{model_id_or_path}': {e}")
        # Safe offline heuristic if network is unavailable and not in local cache
        is_multi = any(tag in name_lower for tag in ["-vl", "_vl", "paligemma", "llava", "pixtral", "florence", "gemma-4"])
        _MODALITY_CACHE[model_id_or_path] = is_multi
        return is_multi


def introspect_ollama_modality(model_name: str, host: str = "http://127.0.0.1:11434", timeout: float = 3.0) -> bool:
    """
    Queries the running Ollama daemon to check if a model's manifest declared families
    include vision capability (e.g. 'clip', 'mllama'), or checks standard vision tags.
    """
    if not model_name:
        return False

    name_lower = model_name.lower()
    # 1. Fast heuristic check for common Ollama vision model tags
    vision_patterns = ["vision", "-vl", ":vl", "moondream", "llava", "minicpm-v", "bakllava"]
    if any(p in name_lower for p in vision_patterns):
        return True

    # 2. Query running Ollama daemon manifest if model is pulled
    import requests
    url = f"{host.rstrip('/')}/api/show"
    try:
        resp = requests.post(url, json={"name": model_name}, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            details = data.get("details", {})
            families = details.get("families", [])
            vision_families = {"clip", "mllama", "vision"}
            for f in families:
                if str(f).lower() in vision_families:
                    return True
    except Exception as e:
        logger.debug(f"Ollama daemon query failed for '{model_name}': {e}")

    return False


def introspect_external_modality(model_name: str) -> bool:
    """
    Checks whether an external API model name supports multimodal/vision inputs.
    """
    if not model_name:
        return False
        
    name_lower = model_name.lower()
    for pattern in KNOWN_EXTERNAL_VISION_PATTERNS:
        if re.search(pattern, name_lower):
            return True
            
    return False


def get_active_modality_status(user: Optional[Any] = None) -> Dict[str, Any]:
    """
    Inspects the currently active LLM backend and model configuration to return
    its verified modality status.
    
    Returns a dict:
        {
            "is_multimodal": bool,
            "backend": str,
            "model_name": str,
            "modality": "multimodal" | "text_only",
            "reason": str
        }
    """
    # 1. Check user-level external model preference first
    if user and not getattr(user, 'is_anonymous', False):
        try:
            from .models import UserActiveModel
            pref = UserActiveModel.objects.filter(user=user, use_external=True).select_related('active_external').first()
            if pref and pref.active_external:
                ext_name = pref.active_external.api_model_name or pref.active_external.name
                is_multi = pref.active_external.is_multimodal or introspect_external_modality(ext_name)
                return {
                    "is_multimodal": is_multi,
                    "backend": "external",
                    "model_name": ext_name,
                    "modality": "multimodal" if is_multi else "text_only",
                    "reason": f"User external model: {ext_name}"
                }
        except Exception as e:
            logger.debug(f"UserActiveModel check error: {e}")

    # 2. Check SystemConfiguration
    try:
        from .models import SystemConfiguration
        config = SystemConfiguration.get_solo()
        if not config:
            return {
                "is_multimodal": False,
                "backend": "none",
                "model_name": "none",
                "modality": "text_only",
                "reason": "No SystemConfiguration found"
            }

        backend = config.hosting_backend

        if backend == "ollama":
            ollama_model = config.active_ollama_model
            model_name = ollama_model.name if ollama_model else (config.custom_ollama_model_name or "unknown")
            is_multi = introspect_ollama_modality(model_name)
            return {
                "is_multimodal": is_multi,
                "backend": "ollama",
                "model_name": model_name,
                "modality": "multimodal" if is_multi else "text_only",
                "reason": f"Ollama model: {model_name}"
            }

        elif backend == "vllm":
            vllm_model = config.active_vllm_model
            model_name = vllm_model.hf_model_id if vllm_model else "unknown"
            is_multi = getattr(vllm_model, "is_multimodal", False) or introspect_hf_modality(model_name)
            return {
                "is_multimodal": is_multi,
                "backend": "vllm",
                "model_name": model_name,
                "modality": "multimodal" if is_multi else "text_only",
                "reason": f"vLLM model: {model_name}"
            }

        elif backend == "pytorch":
            local_model = config.active_local_model
            model_name = local_model.hf_model_id if local_model else "unknown"
            is_multi = getattr(local_model, "is_multimodal", False) or introspect_hf_modality(model_name)
            return {
                "is_multimodal": is_multi,
                "backend": "pytorch",
                "model_name": model_name,
                "modality": "multimodal" if is_multi else "text_only",
                "reason": f"PyTorch local model: {model_name}"
            }

        else:
            return {
                "is_multimodal": False,
                "backend": backend or "none",
                "model_name": "unknown",
                "modality": "text_only",
                "reason": f"Backend '{backend}' has no active model"
            }

    except Exception as e:
        logger.warning(f"Error inspecting active modality status: {e}")
        return {
            "is_multimodal": False,
            "backend": "unknown",
            "model_name": "unknown",
            "modality": "text_only",
            "reason": f"Introspection error: {e}"
        }
