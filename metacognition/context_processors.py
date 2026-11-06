"""
Tool Governance Context Processors (WS17).

Exposes active governance state, ceiling clamps, badge styles, and effective
clearance to all Django templates.
"""

from metacognition.governance import get_governance_summary
import time
import requests

_PRIMARY_HEALTH_CACHE: dict[str, tuple[float, bool]] = {}


def clear_primary_backend_health_cache() -> None:
    _PRIMARY_HEALTH_CACHE.clear()


def is_primary_backend_available_cached(sys_config, ttl_seconds: float = 5.0, timeout: float = 0.25) -> bool:
    """
    Fast, fail-safe check to determine if the active hosting backend is online.
    Caches results in memory for ttl_seconds to prevent blocking web request pipelines.
    """
    import sys
    if not sys_config:
        return False

    backend = getattr(sys_config, "hosting_backend", None)
    if not backend:
        return False

    now = time.monotonic()
    cached = _PRIMARY_HEALTH_CACHE.get(backend)
    if cached is not None and (now - cached[0]) < ttl_seconds:
        return cached[1]

    if "test" in sys.argv:
        return True

    is_online = False
    try:
        if backend == "pytorch":
            from django.conf import settings
            inf_url = getattr(settings, "INFERENCE_URL", "http://127.0.0.1:8001/api/llm")
            ping_url = f"{inf_url.rstrip('/')}/internal/ping/"
            res = requests.get(ping_url, timeout=timeout)
            is_online = (res.status_code == 200)
        elif backend == "llamacpp":
            from llm_api.llamacpp_client import is_llamacpp_available_cached
            is_online = is_llamacpp_available_cached(ttl_seconds=ttl_seconds, timeout=timeout)
        elif backend == "vllm":
            from django.conf import settings
            vllm_url = getattr(settings, "VLLM_BASE_URL", "http://localhost:8000")
            res = requests.get(f"{vllm_url.rstrip('/')}/health", timeout=timeout)
            is_online = (res.status_code == 200)
        elif backend == "ollama":
            from llm_api.ollama_client import OLLAMA_BASE_URL
            res = requests.get(f"{OLLAMA_BASE_URL.rstrip('/')}/api/tags", timeout=timeout)
            is_online = (res.status_code == 200)
    except Exception:
        is_online = False

    _PRIMARY_HEALTH_CACHE[backend] = (now, is_online)
    return is_online


def active_model_context(request):
    """
    Exposes active model info (local vs external provider) and available external models
    to all rendered templates.
    """
    from llm_api.models import SystemConfiguration, UserActiveModel, ExternalAIModel

    user = getattr(request, "user", None)
    sys_config = None
    try:
        sys_config = SystemConfiguration.get_solo()
    except Exception:
        pass

    user_ai_settings = None
    if user and user.is_authenticated:
        try:
            user_ai_settings = UserActiveModel.objects.filter(user=user).select_related("active_external").first()
        except Exception:
            pass

    active_model_info = {
        "is_external": False,
        "name": "No Model Selected",
        "backend": sys_config.hosting_backend if sys_config else "pytorch",
        "provider": "local",
        "status_color": "#94a3b8",
        "badge_icon": "⚪",
        "badge_label": "Local: None"
    }

    if user_ai_settings and user_ai_settings.use_external and user_ai_settings.active_external:
        ext = user_ai_settings.active_external
        active_model_info = {
            "is_external": True,
            "name": ext.name,
            "backend": "external",
            "provider": ext.provider,
            "status_color": "#38bdf8",
            "badge_icon": "⚡",
            "badge_label": f"{ext.provider.capitalize()}: {ext.name}"
        }
    elif sys_config:
        model = None
        if sys_config.hosting_backend == 'pytorch':
            model = sys_config.active_local_model
        elif sys_config.hosting_backend == 'vllm':
            model = sys_config.active_vllm_model
        elif sys_config.hosting_backend == 'ollama':
            model = sys_config.active_ollama_model
        elif sys_config.hosting_backend == 'llamacpp':
            model = getattr(sys_config, 'active_mini_model', None) or sys_config.active_llamacpp_model

        if model:
            is_online = is_primary_backend_available_cached(sys_config)
            active_model_info = {
                "is_external": False,
                "is_online": is_online,
                "name": model.name,
                "backend": sys_config.get_hosting_backend_display(),
                "provider": "local",
                "status_color": "#10b981" if is_online else "#f59e0b",
                "badge_icon": "🟢" if is_online else "🟡",
                "badge_label": f"Local: {model.name}",
                "status_text": "Online" if is_online else "Configured (backend offline / unstarted)",
            }

    mini_model_info = {
        "is_configured": False,
        "is_online": False,
        "name": "No Mini-Model Selected",
        "filename": "",
        "badge_icon": "⚪",
        "badge_label": "Mini: None",
        "status_text": "Unconfigured",
    }
    if sys_config:
        mini_obj = getattr(sys_config, "active_mini_model", None) or getattr(sys_config, "active_llamacpp_model", None)
        if mini_obj:
            from llm_api.llamacpp_client import is_llamacpp_available_cached
            is_online = is_llamacpp_available_cached(ttl_seconds=5.0, timeout=0.25)
            mini_name = mini_obj.name or getattr(mini_obj, "gguf_filename", "Mini-Model")
            mini_file = getattr(mini_obj, "gguf_filename", "") or getattr(mini_obj, "gguf_file_path", "")
            mini_model_info = {
                "is_configured": True,
                "is_online": is_online,
                "name": mini_name,
                "filename": mini_file,
                "badge_icon": "🟢" if is_online else "🟡",
                "badge_label": f"Mini: {mini_name}",
                "status_text": "Online (llama.cpp)" if is_online else "Configured (llama.cpp offline)",
            }

    available_external_models = []
    if user and user.is_authenticated:
        try:
            user_providers = set(user.api_keys.values_list('provider', flat=True))
            if user_providers:
                available_external_models = list(ExternalAIModel.objects.filter(provider__in=user_providers))
        except Exception:
            pass

    return {
        "active_model_info": active_model_info,
        "mini_model_info": mini_model_info,
        "available_external_models": available_external_models,
    }


def governance_context(request):
    """
    Exposes active tool governance state, lockdown level, user clearance,
    and active model information to all rendered templates.
    """
    user = getattr(request, "user", None)
    ctx = {
        "governance": get_governance_summary(user),
    }
    ctx.update(active_model_context(request))
    return ctx

