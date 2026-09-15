import json
import logging
import time
from typing import Generator, Optional
from django.conf import settings

from verbal_tasks.postgres_events import (
    publish_pg_event,
    subscribe_pg_events_sync,
)

logger = logging.getLogger(__name__)


def get_whiteboard_channel(session_id: str | int) -> str:
    return f"verbal_whiteboard_{session_id}"


def publish_whiteboard_event(session_id: str | int, event_type: str, payload: dict) -> bool:
    """
    Publishes a whiteboard mutation event (card_added, card_moved, clustered, ai_stream)
    to PostgreSQL Pub/Sub for real-time synchronization across all participating clients.
    """
    channel = get_whiteboard_channel(session_id)
    payload_with_session = dict(payload)
    payload_with_session["session_id"] = str(session_id)
    return publish_pg_event(channel, event_type, payload_with_session)


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
    Generator that yields real-time SSE events for a whiteboard session over PostgreSQL notifications.
    """
    channel = get_whiteboard_channel(session_id)

    # Initial connection ping
    yield format_datastar_sse("connected", {"session_id": str(session_id), "status": "active"})

    try:
        for event in subscribe_pg_events_sync(channel, timeout=timeout):
            event_type = event.get("event", "message")
            if event_type == "heartbeat":
                yield ": heartbeat\n\n"
                continue

            event_data = event.get("data", {})
            yield format_datastar_sse(event_type, event_data)

    except GeneratorExit:
        logger.debug(f"Whiteboard SSE stream closed for session {session_id}")
    except Exception as e:
        logger.error(f"Whiteboard SSE stream error for session {session_id}: {e}")
