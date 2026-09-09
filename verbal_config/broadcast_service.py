import time
import json
import logging
from typing import Optional, Dict, Any
from django.core.cache import cache

logger = logging.getLogger(__name__)

BROADCAST_CACHE_KEY = "reason_active_broadcast"


class BroadcastService:
    """
    Lightweight broadcast management service for real-time announcements
    and exercise redirects across Demo UI and Admin.
    """

    @classmethod
    def get_current_broadcast(cls) -> Optional[Dict[str, Any]]:
        """Retrieves the active broadcast message, if not expired."""
        data = cache.get(BROADCAST_CACHE_KEY)
        if not data:
            return None
        if data.get("expires_at") and time.time() > data["expires_at"]:
            cache.delete(BROADCAST_CACHE_KEY)
            return None
        return data

    @classmethod
    def set_broadcast(
        cls,
        message: str,
        level: str = "info",
        redirect_url: Optional[str] = None,
        duration_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Sets a new broadcast message."""
        payload = {
            "id": int(time.time() * 1000),
            "message": message,
            "level": level,  # 'info', 'warning', 'alert', 'redirect'
            "redirect_url": redirect_url,
            "created_at": time.time(),
            "expires_at": time.time() + duration_seconds if duration_seconds > 0 else None,
        }
        cache.set(BROADCAST_CACHE_KEY, payload, timeout=duration_seconds or 3600)
        logger.info(f"📣 Broadcast set: {message} (level={level}, redirect={redirect_url})")
        return payload

    @classmethod
    def clear_broadcast(cls):
        """Clears any currently active broadcast."""
        cache.delete(BROADCAST_CACHE_KEY)
        logger.info("📣 Broadcast cleared")
