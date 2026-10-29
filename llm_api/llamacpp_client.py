import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

LLAMACPP_BASE_URL = getattr(settings, "LLAMACPP_BASE_URL", "http://127.0.0.1:8080")


def get_llamacpp_base_url(override_url: str | None = None) -> str:
    """Returns the normalized base URL for the llama.cpp server."""
    base = override_url or getattr(settings, "LLAMACPP_BASE_URL", "http://127.0.0.1:8080")
    return base.rstrip("/")


def is_llamacpp_available(base_url: str | None = None, timeout: float = 1.5) -> bool:
    """
    Fast, fail-safe check to determine if llama-server is online and responsive.
    Queries /health with a short timeout to prevent blocking web threads.
    """
    url = f"{get_llamacpp_base_url(base_url)}/health"
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def get_available_llamacpp_models(base_url: str | None = None, timeout: float = 2.0) -> list[str]:
    """
    Queries the OpenAI-compatible /v1/models endpoint on llama-server.
    Returns a list of registered model identifiers.
    """
    url = f"{get_llamacpp_base_url(base_url)}/v1/models"
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        data = response.json().get("data", [])
        return [m.get("id") for m in data if m.get("id")]
    except requests.exceptions.RequestException as e:
        logger.debug(f"Could not connect to llama.cpp server to fetch models: {e}")
        return []


def get_llamacpp_server_props(base_url: str | None = None, timeout: float = 2.0) -> dict:
    """
    Queries the llama-server /props endpoint for runtime telemetry,
    including context size, active threads, and model configuration.
    """
    url = f"{get_llamacpp_base_url(base_url)}/props"
    try:
        response = requests.get(url, timeout=timeout)
        if response.status_code == 200:
            return response.json()
        return {}
    except requests.exceptions.RequestException as e:
        logger.debug(f"Could not fetch llama.cpp server props: {e}")
        return {}
