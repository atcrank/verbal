import logging
logger = logging.getLogger(__name__)

import requests

# Matches the service name and port in your docker-compose.yml
OLLAMA_BASE_URL = "http://ollama:11434"


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
        requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=5.0)
        action = "Loaded" if active else "Unloaded"
        logger.info(f'✅ {action} {model_name} in Ollama VRAM.')
    except requests.exceptions.RequestException as e:
        logger.info(f'⚠️ Failed to manage Ollama model {model_name}: {e}')