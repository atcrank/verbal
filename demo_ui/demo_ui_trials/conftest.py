import logging
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

logger = logging.getLogger(__name__)

@pytest.fixture(autouse=True)
def setup_demo_ui_trials_env(doctest_namespace, live_server):
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
        """Injects session cookie into Playwright page context for authentic live_server requests."""
        client = Client()
        client.force_login(user)
        session_cookie = client.cookies.get("sessionid")
        if session_cookie:
            page.context.add_cookies([{
                "name": "sessionid",
                "value": session_cookie.value,
                "url": live_server.url,
            }])

    doctest_namespace["authenticate_page"] = authenticate_page

    # Inject service_registry so trials can query which model is active
    from llm_api.apps import service_registry
    doctest_namespace["service_registry"] = service_registry

    # Inject model name helper
    def get_active_model_name():
        """Returns the name of the currently active inference model, or 'unknown'."""
        try:
            ai = service_registry.ai_service
            if ai and hasattr(ai, "model_name"):
                return ai.model_name
            if ai and hasattr(ai, "model_id"):
                return ai.model_id
            return "unknown"
        except Exception:
            return "unknown"

    doctest_namespace["get_active_model_name"] = get_active_model_name
