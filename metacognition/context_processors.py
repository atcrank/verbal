"""
Tool Governance Context Processors (WS17).

Exposes active governance state, ceiling clamps, badge styles, and effective
clearance to all Django templates.
"""

from metacognition.governance import get_governance_summary


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

        if model:
            active_model_info = {
                "is_external": False,
                "name": model.name,
                "backend": sys_config.get_hosting_backend_display(),
                "provider": "local",
                "status_color": "#10b981",
                "badge_icon": "🟢",
                "badge_label": f"Local: {model.name}"
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

