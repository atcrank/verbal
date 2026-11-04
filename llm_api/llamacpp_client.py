import logging
import re
import time
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

LLAMACPP_BASE_URL = getattr(settings, "LLAMACPP_BASE_URL", "http://127.0.0.1:8080")

# Short-lived in-process circuit-breaker cache: {base_url: (timestamp, is_online)}
_HEALTH_CACHE: dict[str, tuple[float, bool]] = {}


def clear_llamacpp_health_cache() -> None:
    """Clears the in-process health check cache (useful in tests)."""
    _HEALTH_CACHE.clear()


def get_llamacpp_base_url(override_url: str | None = None) -> str:
    """Returns the normalized base URL for the llama.cpp server."""
    base = override_url or getattr(settings, "LLAMACPP_BASE_URL", "http://127.0.0.1:8080")
    return base.rstrip("/")


def is_llamacpp_available(base_url: str | None = None, timeout: float = 0.5) -> bool:
    """
    Fast, fail-safe check to determine if llama-server is online and responsive.
    Queries /health with a short timeout to prevent blocking web threads.
    """
    norm_base = get_llamacpp_base_url(base_url)
    url = f"{norm_base}/health"
    try:
        response = requests.get(url, timeout=timeout)
        online = response.status_code == 200
        _HEALTH_CACHE[norm_base] = (time.monotonic(), online)
        return online
    except requests.exceptions.RequestException:
        _HEALTH_CACHE[norm_base] = (time.monotonic(), False)
        return False


def is_llamacpp_available_cached(
    base_url: str | None = None,
    ttl_seconds: float = 5.0,
    timeout: float = 0.3,
) -> bool:
    """
    Non-blocking cached availability check for header bars and utility hooks.
    Avoids repeated socket probes when llama-server is stopped or unconfigured.
    """
    norm_base = get_llamacpp_base_url(base_url)
    now = time.monotonic()
    cached = _HEALTH_CACHE.get(norm_base)
    if cached is not None:
        ts, status = cached
        if (now - ts) < ttl_seconds:
            return status
    return is_llamacpp_available(base_url=norm_base, timeout=timeout)


def get_available_llamacpp_models(base_url: str | None = None, timeout: float = 1.5) -> list[str]:
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


def get_llamacpp_server_props(base_url: str | None = None, timeout: float = 1.5) -> dict:
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


def tokenize_with_llamacpp(
    text: str,
    base_url: str | None = None,
    timeout: float = 0.8,
) -> int | None:
    """
    Counts tokens using llama-server's native POST /tokenize endpoint.
    Fails open immediately (returns None) if llama-server is offline or slow,
    never blocking the caller.
    """
    if not text:
        return 0
    norm_base = get_llamacpp_base_url(base_url)
    cached = _HEALTH_CACHE.get(norm_base)
    if cached is not None and not cached[1] and (time.monotonic() - cached[0]) < 5.0:
        return None

    url = f"{norm_base}/tokenize"
    try:
        response = requests.post(
            url,
            json={"content": text},
            timeout=(min(0.3, timeout), timeout),
        )
        if response.status_code == 200:
            _HEALTH_CACHE[norm_base] = (time.monotonic(), True)
            tokens = response.json().get("tokens")
            if isinstance(tokens, list):
                return len(tokens)
        return None
    except (requests.exceptions.RequestException, ValueError) as e:
        _HEALTH_CACHE[norm_base] = (time.monotonic(), False)
        logger.debug(f"llama.cpp tokenize unavailable (falling back): {e}")
        return None


def fallback_conversation_title(user_prompt: str, max_length: int = 80) -> str:
    """
    Clean deterministic title fallback used immediately on conversation creation
    or when the CPU Mini-Model (llama.cpp) is offline.
    """
    if not user_prompt or not user_prompt.strip():
        return "New Conversation"
    cleaned = " ".join(user_prompt.strip().split())
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    candidate = first_sentence if len(first_sentence) <= max_length else cleaned
    if len(candidate) <= max_length:
        return candidate
    truncated = candidate[:max_length].rsplit(" ", 1)[0]
    return (truncated or candidate[:max_length]).rstrip(".,;:- ") + "..."


def generate_title_with_minimodel(
    user_prompt: str,
    base_url: str | None = None,
    timeout: float = 2.0,
) -> str | None:
    """
    Uses the CPU Mini-Model on llama-server to generate a concise 3-6 word
    Conversation Title. Returns None immediately if llama-server is offline,
    unconfigured, or exceeds the short timeout.
    """
    if not user_prompt or not user_prompt.strip():
        return None

    norm_base = get_llamacpp_base_url(base_url)
    if not is_llamacpp_available_cached(base_url=norm_base, ttl_seconds=5.0, timeout=0.3):
        return None

    snippet = " ".join(user_prompt.strip().split())[:400]
    url = f"{norm_base}/v1/chat/completions"
    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "Generate a concise 3 to 6 word title summarizing the user's request. "
                    "Reply with ONLY the title text, no quotes or punctuation."
                ),
            },
            {"role": "user", "content": snippet},
        ],
        "max_tokens": 24,
        "temperature": 0.2,
        "stream": False,
    }
    try:
        response = requests.post(
            url,
            json=payload,
            timeout=(min(0.3, timeout), timeout),
        )
        if response.status_code != 200:
            return None
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        raw_title = (choices[0].get("message", {}).get("content") or "").strip()
        # Strip any <think>...</think> blocks or surrounding quotes/prefixes
        raw_title = re.sub(r"<think>.*?</think>", "", raw_title, flags=re.DOTALL).strip()
        raw_title = re.sub(r"^(title\s*:\s*)", "", raw_title, flags=re.IGNORECASE).strip()
        raw_title = raw_title.strip("\"'`*# \n\r\t")
        first_line = raw_title.splitlines()[0].strip() if raw_title else ""
        if not first_line:
            return None
        if len(first_line) > 80:
            first_line = first_line[:80].rsplit(" ", 1)[0] + "..."
        return first_line
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        logger.debug(f"Mini-Model title generation skipped/fell back: {e}")
        return None


def maybe_refine_conversation_title(conversation_id: str, user_prompt: str) -> str | None:
    """
    Non-blocking helper that refines a Conversation's title using the active
    CPU Mini-Model if enabled in SystemConfiguration and llama-server is online.
    """
    try:
        from .models import SystemConfiguration, Conversation
        config = SystemConfiguration.get_solo()
        if not config or not getattr(config, "use_mini_model_for_utilities", True):
            return None
        has_mini = bool(
            getattr(config, "active_mini_model_id", None)
            or getattr(config, "active_llamacpp_model_id", None)
            or config.hosting_backend == SystemConfiguration.HostingBackend.LLAMACPP
        )
        if not has_mini:
            return None

        title = generate_title_with_minimodel(user_prompt)
        if title:
            Conversation.objects.filter(id=conversation_id).update(title=title)
            return title
    except Exception as e:
        logger.debug(f"maybe_refine_conversation_title non-fatal error: {e}")
    return None
