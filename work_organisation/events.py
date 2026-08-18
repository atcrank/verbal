import json
import logging
import time
from typing import Generator, Optional
from django.conf import settings

logger = logging.getLogger(__name__)

# Redis Client for Whiteboard Pub/Sub
try:
    import redis
    redis_url = getattr(settings, 'CELERY_BROKER_URL', 'redis://127.0.0.1:6379/0')
    redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
except Exception as e:
    logger.warning(f"Redis not available for Whiteboard events: {e}")
    redis_client = None


def get_whiteboard_channel(session_id: str | int) -> str:
    return f"verbal:whiteboard:{session_id}"


def publish_whiteboard_event(session_id: str | int, event_type: str, payload: dict) -> bool:
    """
    Publishes a whiteboard mutation event (card_added, card_moved, clustered, ai_stream)
    to Redis Pub/Sub for real-time synchronization across all participating clients.
    """
    if not redis_client:
        logger.debug(f"Redis offline, skipping whiteboard publish: {event_type}")
        return False
    try:
        channel = get_whiteboard_channel(session_id)
        msg = json.dumps({
            "session_id": str(session_id),
            "event_type": event_type,
            "payload": payload,
            "timestamp": time.time()
        })
        redis_client.publish(channel, msg)
        return True
    except Exception as e:
        logger.error(f"Error publishing whiteboard event: {e}")
        return False


def format_datastar_sse(event_type: str, data: dict, fragment_html: Optional[str] = None) -> str:
    """
    Formats an event for Datastar SSE consumption.
    Supports both signal merges and fragment merges.
    """
    lines = []
    if fragment_html:
        lines.append("event: datastar-merge-fragments")
        for line in fragment_html.split("\n"):
            lines.append(f"data: fragments {line}")
    else:
        lines.append(f"event: {event_type}")
        lines.append(f"data: {json.dumps(data)}")

    lines.append("\n")
    return "\n".join(lines)


def stream_whiteboard_events(session_id: str | int, timeout: int = 30) -> Generator[str, None, None]:
    """
    Generator that yields real-time SSE events for a whiteboard session.
    """
    if not redis_client:
        yield format_datastar_sse("error", {"error": "Redis broker offline"})
        return

    channel = get_whiteboard_channel(session_id)
    pubsub = redis_client.pubsub()
    pubsub.subscribe(channel)

    # Initial connection ping
    yield format_datastar_sse("connected", {"session_id": str(session_id), "status": "active"})

    start_time = time.time()
    try:
        while True:
            msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg.get("type") == "message":
                try:
                    payload = json.loads(msg["data"])
                    event_type = payload.get("event_type", "message")
                    event_data = payload.get("payload", {})
                    yield format_datastar_sse(event_type, event_data)
                except Exception as e:
                    logger.error(f"Error parsing whiteboard SSE message: {e}")

            # Keep-alive heartbeat every 15s
            if time.time() - start_time > 15:
                yield ": heartbeat\n\n"
                start_time = time.time()

    except GeneratorExit:
        pubsub.unsubscribe(channel)
        pubsub.close()
    except Exception as e:
        logger.error(f"Whiteboard SSE stream error: {e}")
        pubsub.unsubscribe(channel)
        pubsub.close()
