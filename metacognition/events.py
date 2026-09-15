"""
Event dispatch and PostgreSQL Pub/Sub bridge for asynchronous LangGraph execution.
Facilitates zero-Redis communication between background task workers and ASGI Datastar SSE streams.
"""
import logging
from typing import Dict, Any, Optional, AsyncGenerator

from verbal_tasks.postgres_events import (
    publish_pg_event,
    subscribe_pg_events_async,
    set_runtime_flag,
    is_runtime_flag_set,
    clear_runtime_flag
)

logger = logging.getLogger(__name__)


def get_event_channel(run_id: str) -> str:
    return f"verbal_events_{run_id}"


def get_cancel_key(run_id: str) -> str:
    return f"verbal_cancel_{run_id}"


def publish_blueprint_event(run_id: str, event_type: str, payload: Dict[str, Any]) -> bool:
    """
    Publishes an execution event to the PostgreSQL channel for the given run_id.
    """
    if not run_id:
        return False

    channel = get_event_channel(run_id)
    return publish_pg_event(channel, event_type, payload)


def set_cancellation_flag(run_id: str, ttl: int = 600) -> None:
    """
    Sets a cancellation flag for the given run_id and broadcasts a cancellation event.
    """
    if not run_id:
        return
    set_runtime_flag(get_cancel_key(run_id), "1", ttl=ttl)
    publish_blueprint_event(run_id, "cancelled", {"message": "Execution cancelled by user."})
    logger.info(f"Cancellation flag set for run_id: {run_id}")


def is_cancelled(run_id: Optional[str]) -> bool:
    """
    Checks if a cancellation flag has been set for the given run_id.
    """
    if not run_id:
        return False
    return is_runtime_flag_set(get_cancel_key(run_id))


def clear_cancellation_flag(run_id: str) -> None:
    """
    Clears the cancellation flag for a run_id.
    """
    if not run_id:
        return
    clear_runtime_flag(get_cancel_key(run_id))


async def subscribe_blueprint_events(run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Async generator subscribing to the PostgreSQL event channel for run_id
    and yielding parsed event dictionaries.
    """
    if not run_id:
        return

    channel_name = get_event_channel(run_id)
    async for event in subscribe_pg_events_async(channel_name):
        yield event
