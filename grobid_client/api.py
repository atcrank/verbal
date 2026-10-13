import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def is_grobid_available(timeout: float = 1.0) -> bool:
    """
    Fast, non-blocking circuit-breaker check to determine if the local GROBID daemon is alive.
    Checks /api/isalive with a short timeout.
    """
    grobid_url = getattr(settings, "GROBID_URL", "http://localhost:8070")
    try:
        response = requests.get(f"{grobid_url.rstrip('/')}/api/isalive", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def process_pdf_with_grobid(file_path: str) -> str:
    """
    Sends a PDF to the local Grobid container and returns the TEI XML.
    """
    grobid_url = getattr(settings, "GROBID_URL", "http://localhost:8070")
    if not is_grobid_available(timeout=1.0):
        raise ConnectionError(f"Connection to Grobid at {grobid_url} refused (daemon offline)")

    url = f"{grobid_url.rstrip('/')}/api/processFulltextDocument"

    try:
        with open(file_path, 'rb') as f:
            files = {'input': f}
            # processFulltextDocument extracts header, body, and bibliography
            response = requests.post(url, files=files, timeout=300)
            response.raise_for_status()
            return response.text
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Failed to process document with Grobid at {grobid_url}: {e}")