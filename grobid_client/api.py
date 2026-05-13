import requests
from django.conf import settings


def process_pdf_with_grobid(file_path: str) -> str:
    """
    Sends a PDF to the local Grobid container and returns the TEI XML.
    """
    # Default to the port mapped in your docker-compose.yml
    grobid_url = getattr(settings, "GROBID_URL", "http://localhost:8070")
    url = f"{grobid_url.rstrip('/')}/api/processFulltextDocument"

    try:
        with open(file_path, 'rb') as f:
            files = {'input': f}
            # processFulltextDocument extracts header, body, and bibliography
            response = requests.post(url, files=files, timeout=300)
            response.raise_for_status()
            return response.text
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Failed to process document with Grobid: {e}")