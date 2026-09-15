"""
PostgreSQL-backed event distribution (Pub/Sub) and runtime state bridge.
Provides zero-Redis real-time event broadcasting using PostgreSQL LISTEN/NOTIFY
and psycopg (v3) for both synchronous and asynchronous consumers.
"""
import os
import re
import json
import time
import logging
import asyncio
import threading
from typing import Dict, Any, Optional, Generator, AsyncGenerator
from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)

# Fallback in-memory event queues for test environments (e.g. SQLite / mock tests)
_in_memory_subscribers: Dict[str, list] = {}
_in_memory_lock = threading.Lock()

# In-memory runtime flags (with expiration timestamps) for cancellation & state tokens
_runtime_flags: Dict[str, Dict[str, Any]] = {}
_runtime_flags_lock = threading.Lock()


def sanitize_channel_name(channel: str) -> str:
    """
    Sanitizes channel names to match valid PostgreSQL identifier rules (letters, digits, underscores, max 63 chars).
    """
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', str(channel))
    if not sanitized or not (sanitized[0].isalpha() or sanitized[0] == '_'):
        sanitized = f"ch_{sanitized}"
    return sanitized[:63]


def get_pg_connection_params() -> dict:
    """
    Extracts psycopg connection kwargs from Django settings, honoring active test database names.
    """
    db_config = getattr(connection, 'settings_dict', settings.DATABASES.get('default', {}))
    params = {
        'dbname': db_config.get('NAME', 'verbal_db'),
        'user': db_config.get('USER', 'verbal_user'),
        'password': db_config.get('PASSWORD', 'verbal_password'),
        'host': db_config.get('HOST', 'localhost'),
        'port': int(db_config.get('PORT', 5433)),
    }
    return params


def publish_pg_event(channel: str, event_type: str, payload: dict) -> bool:
    """
    Publishes an event to a PostgreSQL channel using pg_notify.
    Also dispatches to any in-memory subscribers (useful for tests or non-PG environments).
    """
    from django.core.exceptions import SynchronousOnlyOperation

    channel_name = sanitize_channel_name(channel)
    envelope = {
        "event": event_type,
        "channel": channel_name,
        "timestamp": time.time(),
        "data": payload
    }
    payload_str = json.dumps(envelope)

    # 1. Dispatch to in-memory fallback subscribers
    with _in_memory_lock:
        if channel_name in _in_memory_subscribers:
            for q in list(_in_memory_subscribers[channel_name]):
                try:
                    if isinstance(q, asyncio.Queue):
                        q.put_nowait(envelope)
                    else:
                        q.append(envelope)
                except Exception as ex:
                    logger.debug(f"Error dispatching to in-memory subscriber: {ex}")

    # 2. Check if running against PostgreSQL
    is_postgres = getattr(connection, 'vendor', '') == 'postgresql'
    if not is_postgres:
        logger.debug(f"Non-Postgres DB ({getattr(connection, 'vendor', 'unknown')}). Dispatched in-memory.")
        return True

    # 3. Handle PostgreSQL 8000-byte limit by truncating oversized previews if necessary
    if len(payload_str.encode('utf-8')) > 7800:
        logger.warning(f"Event payload exceeds 7800 bytes on channel '{channel_name}'. Truncating detailed fields.")
        safe_payload = dict(payload)
        for k in ('output', 'final_response', 'data', 'text'):
            if k in safe_payload and isinstance(safe_payload[k], str) and len(safe_payload[k]) > 2000:
                safe_payload[k] = safe_payload[k][:2000] + "... [truncated in stream notification]"
        envelope["data"] = safe_payload
        payload_str = json.dumps(envelope)

    def _execute_notify():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_notify(%s, %s);", [channel_name, payload_str])

    try:
        _execute_notify()
        return True
    except SynchronousOnlyOperation:
        # When called from an async loop/thread, execute notify in a background thread
        thread = threading.Thread(target=_execute_notify)
        thread.start()
        thread.join(timeout=1.0)
        return True
    except Exception as e:
        logger.warning(f"Failed to execute pg_notify on channel '{channel_name}': {e}")
        return False


def subscribe_pg_events_sync(channel: str, timeout: float = 30.0) -> Generator[Dict[str, Any], None, None]:
    """
    Synchronous generator listening for PostgreSQL notifications on a channel.
    Falls back gracefully to in-memory queue if PostgreSQL connection is not available.
    """
    channel_name = sanitize_channel_name(channel)
    is_postgres = getattr(connection, 'vendor', '') == 'postgresql'

    # Check if psycopg is available and we can connect to PostgreSQL
    if is_postgres:
        try:
            import psycopg
            conn_params = get_pg_connection_params()
            with psycopg.connect(**conn_params, autocommit=True) as conn:
                conn.execute(f'LISTEN "{channel_name}";')
                logger.debug(f"Sync LISTEN active on channel: {channel_name}")

                gen = conn.notifies(timeout=1.0)
                last_active = time.time()
                while True:
                    notify = None
                    try:
                        notify = next(gen)
                    except StopIteration:
                        gen = conn.notifies(timeout=1.0)
                    except Exception as loop_ex:
                        logger.debug(f"Sync notifies iteration check: {loop_ex}")

                    if notify:
                        try:
                            data = json.loads(notify.payload)
                            yield data
                            last_active = time.time()
                            if data.get("event") in ("completed", "cancelled", "error"):
                                break
                        except Exception as parse_ex:
                            logger.error(f"Failed to parse notify payload: {parse_ex}")

                    # Yield keep-alive signal periodically
                    if time.time() - last_active > 15.0:
                        yield {"event": "heartbeat", "data": {}}
                        last_active = time.time()

                    time.sleep(0.1)
            return
        except Exception as e:
            logger.warning(f"Failed to establish sync PostgreSQL notification listener: {e}. Falling back to in-memory.")

    # In-memory queue fallback
    queue = []
    with _in_memory_lock:
        _in_memory_subscribers.setdefault(channel_name, []).append(queue)

    try:
        last_heartbeat = time.time()
        while True:
            while queue:
                item = queue.pop(0)
                yield item
                if item.get("event") in ("completed", "cancelled", "error"):
                    return

            if time.time() - last_heartbeat > 15.0:
                yield {"event": "heartbeat", "data": {}}
                last_heartbeat = time.time()

            time.sleep(0.2)
    finally:
        with _in_memory_lock:
            if channel_name in _in_memory_subscribers and queue in _in_memory_subscribers[channel_name]:
                _in_memory_subscribers[channel_name].remove(queue)


async def subscribe_pg_events_async(channel: str, timeout: float = 30.0) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Asynchronous generator listening for PostgreSQL notifications via psycopg.AsyncConnection.
    Falls back gracefully to asyncio.Queue if PostgreSQL connection is not available.
    """
    channel_name = sanitize_channel_name(channel)
    is_postgres = getattr(connection, 'vendor', '') == 'postgresql'

    if is_postgres:
        try:
            import psycopg
            conn_params = get_pg_connection_params()
            async with await psycopg.AsyncConnection.connect(**conn_params, autocommit=True) as aconn:
                await aconn.execute(f'LISTEN "{channel_name}";')
                logger.debug(f"Async LISTEN active on channel: {channel_name}")

                gen = aconn.notifies(timeout=1.0)
                last_active = time.time()

                while True:
                    try:
                        notify = await anext(gen)
                    except StopAsyncIteration:
                        gen = aconn.notifies(timeout=1.0)
                        notify = None
                    except Exception as loop_ex:
                        logger.debug(f"Async notifies iteration check: {loop_ex}")
                        notify = None

                    if notify:
                        try:
                            data = json.loads(notify.payload)
                            yield data
                            last_active = time.time()
                            if data.get("event") in ("completed", "cancelled", "error"):
                                break
                        except Exception as parse_ex:
                            logger.error(f"Failed to parse notify payload: {parse_ex}")

                    if time.time() - last_active > 15.0:
                        yield {"event": "heartbeat", "data": {}}
                        last_active = time.time()

                    await asyncio.sleep(0.1)
            return
        except Exception as e:
            logger.warning(f"Failed to establish async PostgreSQL notification listener: {e}. Falling back to in-memory.")

    # In-memory asyncio queue fallback
    queue: asyncio.Queue = asyncio.Queue()
    with _in_memory_lock:
        _in_memory_subscribers.setdefault(channel_name, []).append(queue)

    try:
        last_heartbeat = time.time()
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield item
                if item.get("event") in ("completed", "cancelled", "error"):
                    return
            except asyncio.TimeoutError:
                pass

            if time.time() - last_heartbeat > 15.0:
                yield {"event": "heartbeat", "data": {}}
                last_heartbeat = time.time()
    finally:
        with _in_memory_lock:
            if channel_name in _in_memory_subscribers and queue in _in_memory_subscribers[channel_name]:
                _in_memory_subscribers[channel_name].remove(queue)


# ==========================================
# RUNTIME FLAGS (CANCELLATION & EPHEMERAL STATE)
# ==========================================

def set_runtime_flag(key: str, value: str = "1", ttl: int = 600) -> None:
    """
    Sets an ephemeral runtime flag with an expiration time.
    Replaces Redis key-value storage for cancellation flags.
    """
    expires_at = time.time() + ttl
    with _runtime_flags_lock:
        _runtime_flags[str(key)] = {
            "value": value,
            "expires_at": expires_at
        }
    logger.debug(f"Runtime flag set: '{key}' (expires in {ttl}s)")


def get_runtime_flag(key: str) -> Optional[str]:
    """
    Retrieves a runtime flag if set and not expired.
    """
    with _runtime_flags_lock:
        entry = _runtime_flags.get(str(key))
        if not entry:
            return None
        if time.time() > entry["expires_at"]:
            _runtime_flags.pop(str(key), None)
            return None
        return entry["value"]


def is_runtime_flag_set(key: str) -> bool:
    """
    Checks whether a runtime flag exists and is unexpired.
    """
    return get_runtime_flag(key) is not None


def clear_runtime_flag(key: str) -> None:
    """
    Clears a runtime flag.
    """
    with _runtime_flags_lock:
        _runtime_flags.pop(str(key), None)
