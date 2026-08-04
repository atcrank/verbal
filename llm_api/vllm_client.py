import logging
import subprocess
import os

from django.conf import settings

logger = logging.getLogger(__name__)

def start_container(model_id: str):
    """Starts the vLLM Docker container using docker compose."""
    if not model_id:
        logger.warning("No model ID provided for vLLM container startup.")
        return

    try:
        logger.info(f"Starting vLLM container for model {model_id} via docker compose...")
        env = os.environ.copy()
        env["VLLM_MODEL"] = model_id
        subprocess.run(
            ["docker", "compose", "-f", str(settings.BASE_DIR / "docker-compose.yml"), "up", "-d", "vllm"],
            check=True, capture_output=True, text=True, env=env
        )
        logger.info(f"✅ vLLM container started for model {model_id}.")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Failed to start vLLM container: {e.stderr}")

def stop_container():
    """Stops the vLLM Docker container using docker compose."""
    try:
        logger.info("Stopping vLLM container via docker compose...")
        subprocess.run(
            ["docker", "compose", "-f", str(settings.BASE_DIR / "docker-compose.yml"), "stop", "vllm"],
            check=True, capture_output=True, text=True
        )
        logger.info("✅ vLLM container stopped.")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Failed to stop vLLM container: {e.stderr}")
