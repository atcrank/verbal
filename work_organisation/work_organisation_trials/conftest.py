import logging
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def setup_work_organisation_trials_env(doctest_namespace, live_server):
    """
    Injects the running live_server instance and URL into the doctest global namespace.
    Also provides an authenticate_page helper to inject real Django session cookies
    directly into Playwright browser contexts for authenticated live_server requests.
    """
    from django.conf import settings
    settings.TASKS["default"]["BACKEND"] = "django.tasks.backends.immediate.ImmediateBackend"
    settings.LLM_REQUEST_TIMEOUT = 3600

    doctest_namespace["live_server"] = live_server
    doctest_namespace["live_server_url"] = live_server.url

    def authenticate_page(page, user):
        """Injects session and CSRF cookies into Playwright page context for authentic live_server requests."""
        client = Client(enforce_csrf_checks=False)
        client.force_login(user)
        session_cookie = client.cookies.get("sessionid")
        cookies = []
        if session_cookie:
            cookies.append({
                "name": "sessionid",
                "value": session_cookie.value,
                "url": live_server.url,
            })
        cookies.append({
            "name": "csrftoken",
            "value": "playwright_csrf_token_safe_val_12345",
            "url": live_server.url,
        })
        page.context.add_cookies(cookies)

    doctest_namespace["authenticate_page"] = authenticate_page

    from llm_api.apps import service_registry
    doctest_namespace["service_registry"] = service_registry

    def get_active_model_name():
        """Returns the identifier of the currently active inference model, or 'unknown'."""
        try:
            from llm_api.modality_detector import get_active_modality_status
            status = get_active_modality_status()
            name = status.get("model_name")
            if name and name not in ("none", "unknown"):
                return name
        except Exception:
            pass
        try:
            ai = service_registry.ai_service
            if ai and getattr(ai, "model_id", None):
                return ai.model_id
            if ai and getattr(ai, "model_name", None):
                return ai.model_name
        except Exception:
            pass
        return "unknown"

    doctest_namespace["get_active_model_name"] = get_active_model_name
