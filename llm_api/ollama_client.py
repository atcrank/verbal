import logging
logger = logging.getLogger(__name__)

import requests

from django.conf import settings

# Matches the service name and port in your docker-compose.yml (or overridden in settings)
OLLAMA_BASE_URL = getattr(settings, "OLLAMA_BASE_URL", "http://ollama:11434")


def get_available_ollama_models():
    """Fetches the list of models currently downloaded in the Ollama container."""
    try:
        # Query the local Ollama API for downloaded tags
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2.0)
        response.raise_for_status()
        models = response.json().get("models", [])
        return [m["name"] for m in models]
    except requests.exceptions.RequestException as e:
        logger.info(f'⚠️ Could not connect to Ollama to fetch models: {e}')
        return []


def set_ollama_model_state(model_name: str, active: bool):
    """
    Loads or unloads an Ollama model from GPU VRAM.
    keep_alive: -1 keeps it in VRAM indefinitely. 0 unloads it immediately.
    """
    if not model_name:
        return

    payload = {
        "model": model_name,
        "keep_alive": -1 if active else 0
    }

    try:
        # Pinging the generate endpoint with keep_alive manages the VRAM
        # without needing to send an actual generation prompt.
        # Cold-loading large multimodal models (e.g. Gemma 4 E2B + mmproj) can exceed 5s.
        req_timeout = 90.0 if active else 10.0
        requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=req_timeout)
        action = "Loaded" if active else "Unloaded"
        logger.info(f'✅ {action} {model_name} in Ollama VRAM.')
    except requests.exceptions.RequestException as e:
        logger.info(f'⚠️ Failed to manage Ollama model {model_name}: {e}')

from .diagnostics import run_docker_compose_action


def start_container(pull_policy: str = "never") -> tuple[bool, str]:
    """Starts the Ollama Docker container using docker compose without pulling or rebuilding."""
    logger.info("Starting Ollama container via docker compose...")
    args = ["up", "-d", "--no-build"]
    if pull_policy:
        args.extend(["--pull", pull_policy])
    args.append("ollama")
    ok, detail = run_docker_compose_action(args, "Start Ollama container")
    if ok:
        logger.info("✅ Ollama container started.")
    return ok, detail


def stop_container() -> tuple[bool, str]:
    """Stops the Ollama Docker container using docker compose."""
    logger.info("Stopping Ollama container via docker compose...")
    ok, detail = run_docker_compose_action(["stop", "ollama"], "Stop Ollama container")
    if ok:
        logger.info("✅ Ollama container stopped.")
    return ok, detail