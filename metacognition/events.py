"""
Event dispatch and Pub/Sub bridge for asynchronous LangGraph execution.
Facilitates communication between Celery workers and ASGI SSE streams.
"""
import os
import json
import time
import logging
from typing import Dict, Any, Optional, AsyncGenerator

import redis
import redis.asyncio as aioredis
from django.conf import settings

logger = logging.getLogger(__name__)

REDIS_URL = getattr(settings, "REDIS_URL", getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0"))


def get_redis_client():
    """Returns a synchronous Redis connection pool client."""
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def get_async_redis_client():
    """Returns an asynchronous Redis connection pool client."""
    return aioredis.from_url(REDIS_URL, decode_responses=True)


def get_event_channel(run_id: str) -> str:
    return f"verbal:events:{run_id}"


def get_cancel_key(run_id: str) -> str:
    return f"verbal:cancel:{run_id}"


def publish_blueprint_event(run_id: str, event_type: str, payload: Dict[str, Any]):
    """
    Publishes an execution event to the Redis channel for the given run_id.
    """
    if not run_id:
        return

    event_payload = {
        "event": event_type,
        "run_id": run_id,
        "timestamp": time.time(),
        "data": payload
    }
    channel = get_event_channel(run_id)
    
    try:
        client = get_redis_client()
        client.publish(channel, json.dumps(event_payload))
    except Exception as e:
        logger.warning(f"Failed to publish event '{event_type}' to Redis channel '{channel}': {e}")


def set_cancellation_flag(run_id: str, ttl: int = 600):
    """
    Sets a cancellation flag in Redis for the given run_id.
    """
    if not run_id:
        return
    try:
        client = get_redis_client()
        client.setex(get_cancel_key(run_id), ttl, "1")
        # Also broadcast cancellation event immediately to the stream
        publish_blueprint_event(run_id, "cancelled", {"message": "Execution cancelled by user."})
        logger.info(f"Cancellation flag set for run_id: {run_id}")
    except Exception as e:
        logger.warning(f"Failed to set cancellation flag for run_id '{run_id}': {e}")


def is_cancelled(run_id: Optional[str]) -> bool:
    """
    Checks if a cancellation flag has been set in Redis for the given run_id.
    """
    if not run_id:
        return False
    try:
        client = get_redis_client()
        return bool(client.exists(get_cancel_key(run_id)))
    except Exception as e:
        logger.warning(f"Failed to check cancellation flag for run_id '{run_id}': {e}")
        return False


def clear_cancellation_flag(run_id: str):
    """
    Clears the cancellation flag for a run_id.
    """
    if not run_id:
        return
    try:
        client = get_redis_client()
        client.delete(get_cancel_key(run_id))
    except Exception as e:
        logger.warning(f"Failed to clear cancellation flag for run_id '{run_id}': {e}")


async def subscribe_blueprint_events(run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Async generator that subscribes to the Redis event channel for run_id
    and yields parsed event dictionaries.
    """
    if not run_id:
        return

    channel_name = get_event_channel(run_id)
    client = get_async_redis_client()
    pubsub = client.pubsub()

    try:
        await pubsub.subscribe(channel_name)
        logger.info(f"Subscribed to async Redis channel: {channel_name}")

        while True:
            # Check for cancellation or messages with timeout
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                try:
                    data = json.loads(message["data"])
                    yield data
                    # If final completion or cancellation or error, close stream
                    if data.get("event") in ("completed", "error", "cancelled"):
                        break
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.error(f"Error in Redis subscription for {channel_name}: {e}")
    finally:
        try:
            await pubsub.unsubscribe(channel_name)
            await client.aclose()
        except Exception:
            pass
