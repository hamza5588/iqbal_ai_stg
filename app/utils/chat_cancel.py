"""Cancel an in-flight RAG chat turn when the user leaves the thread.

A run id is assigned at the start of each /api/rag/chat invoke. Cancel records that
run id so a later turn on the same thread is not aborted by a stale flag.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from urllib.parse import urlparse

try:
    import redis
except Exception:  # pragma: no cover
    redis = None

logger = logging.getLogger(__name__)

_redis_client = None
_TTL_SECONDS = 600

_mem_guard = threading.Lock()
_active_runs: dict = {}
_cancelled_runs: dict = {}


class ChatTurnCancelled(Exception):
    """The teacher left the chat (or asked to cancel) while this turn was still running."""


def _get_redis_client():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if redis is None:
        return None
    redis_url = (
        os.getenv("CHAT_LOCK_REDIS_URL")
        or os.getenv("CHAT_PROGRESS_REDIS_URL")
        or os.getenv("CELERY_BROKER_URL")
        or "redis://localhost:6379/0"
    )
    try:
        parsed = urlparse(redis_url)
        if not parsed.scheme.startswith("redis"):
            return None
        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
            health_check_interval=30,
        )
        client.ping()
        _redis_client = client
    except Exception as exc:
        logger.debug("Chat cancel: Redis unavailable (%s); using in-process flags.", exc)
        _redis_client = None
    return _redis_client


def _active_key(thread_id: str) -> str:
    return f"chat_active_run:{thread_id}"


def _cancel_key(thread_id: str) -> str:
    return f"chat_cancel_run:{thread_id}"


def start_chat_run(thread_id: str) -> str:
    """Mark a new turn as active. Returns the run id to stash on LangGraph config."""
    run_id = uuid.uuid4().hex
    if not thread_id:
        return run_id
    client = _get_redis_client()
    if client is not None:
        try:
            client.set(_active_key(thread_id), run_id, ex=_TTL_SECONDS)
            return run_id
        except Exception as exc:
            logger.debug("Chat cancel: failed to store active run: %s", exc)
    with _mem_guard:
        _active_runs[thread_id] = run_id
    return run_id


def request_cancel(thread_id: str) -> None:
    """Cancel whichever run is currently active for this thread."""
    if not thread_id:
        return
    client = _get_redis_client()
    if client is not None:
        try:
            run_id = client.get(_active_key(thread_id))
            if run_id:
                if isinstance(run_id, bytes):
                    run_id = run_id.decode("utf-8", errors="replace")
                client.set(_cancel_key(thread_id), run_id, ex=_TTL_SECONDS)
            return
        except Exception as exc:
            logger.debug("Chat cancel: failed to set cancel flag: %s", exc)
    with _mem_guard:
        run_id = _active_runs.get(thread_id)
        if run_id:
            _cancelled_runs[thread_id] = run_id


def is_chat_run_cancelled(thread_id: str, run_id: str) -> bool:
    if not thread_id or not run_id:
        return False
    client = _get_redis_client()
    if client is not None:
        try:
            cancelled = client.get(_cancel_key(thread_id))
            if cancelled is None:
                return False
            if isinstance(cancelled, bytes):
                cancelled = cancelled.decode("utf-8", errors="replace")
            return str(cancelled) == str(run_id)
        except Exception as exc:
            logger.debug("Chat cancel: failed to read cancel flag: %s", exc)
    with _mem_guard:
        return _cancelled_runs.get(thread_id) == run_id
