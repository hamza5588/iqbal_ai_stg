"""
Cross-process "one turn at a time per chat thread" lock.

The original implementation (`_user_chat_locks` in app/routes/rag_routes.py) was a plain
in-process `dict` of `threading.Lock()` keyed by user_id. Gunicorn runs multiple *worker
processes* (see CLAUDE.md - 9 workers x 8 threads), each with its own independent Python
memory space, so that lock only ever serialized requests that happened to land on the same
worker. Two requests for the same thread landing on two different workers ran the LangGraph
turn concurrently against the same Postgres-checkpointed thread state, racing on whatever
code assembled the final HTTP response - confirmed live (QA sweep) to actually deliver one
user's answer to a different, unrelated request.

Redis-backed (same URL-resolution convention as app/utils/chat_progress.py) so the lock is
visible to every worker. Keyed by thread_id rather than user_id: the actual race is two
requests hammering the same LangGraph thread's checkpointed state, and thread_id is the
narrower, more correct key - it also means a user with two legitimate chat tabs open on two
different documents no longer contends with themselves unnecessarily.

If Redis is unavailable, falls back to the original in-process threading.Lock() dict. That
still protects the same-worker case (the majority case for a double-click / accidental double
send) and is never worse than the pre-fix behavior - only the "different requests on different
workers" case goes unprotected in that degraded mode, same as before this fix existed.
"""
import logging
import os
import threading
from typing import Any, Optional
from urllib.parse import urlparse

try:
    import redis
    from redis.exceptions import LockError, LockNotOwnedError
except Exception:  # pragma: no cover - optional dependency fallback
    redis = None
    LockError = LockNotOwnedError = Exception

logger = logging.getLogger(__name__)

_redis_client = None
_redis_unavailable_logged = False

# Auto-release safety net if a worker dies/crashes mid-turn without releasing - must comfortably
# exceed the longest legitimate turn (multi-round lesson generation has been observed taking
# several minutes) so it never expires out from under a still-running turn.
_LOCK_AUTO_EXPIRE_SECONDS = int(os.getenv("RAG_CHAT_LOCK_AUTO_EXPIRE_SECONDS", "600"))

# In-process fallback, only used when Redis is unavailable.
_fallback_locks: dict = {}
_fallback_locks_guard = threading.Lock()


def _get_redis_client():
    global _redis_client, _redis_unavailable_logged
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
            socket_timeout=2.0,
            health_check_interval=30,
        )
        client.ping()
        _redis_client = client
        logger.info("Chat lock: using Redis backend at %s", redis_url)
    except Exception as exc:
        if not _redis_unavailable_logged:
            logger.warning(
                "Chat lock: Redis unavailable (%s); falling back to in-process lock "
                "(only same-worker requests will be serialized).",
                exc,
            )
            _redis_unavailable_logged = True
        _redis_client = None
    return _redis_client


class _FallbackLockHandle:
    """Wraps the pre-fix threading.Lock() behavior so callers don't need to know which
    backend is in play."""

    def __init__(self, lock: threading.Lock):
        self._lock = lock

    def release(self) -> None:
        try:
            self._lock.release()
        except RuntimeError:
            # Already released / not held - never let lock cleanup break the response.
            pass


def acquire_chat_lock(key: str, timeout_seconds: float) -> Optional[Any]:
    """
    Blocks up to timeout_seconds trying to acquire the lock for `key` (typically thread_id).
    Returns an opaque handle to pass to release_chat_lock() on success, or None if the wait
    timed out (caller should treat this exactly like the old lock.acquire() timeout - reply
    with a 429 "still generating" response rather than proceeding).
    """
    client = _get_redis_client()
    if client is not None:
        lock = client.lock(
            f"chat_lock:{key}",
            timeout=_LOCK_AUTO_EXPIRE_SECONDS,
            blocking_timeout=timeout_seconds,
        )
        try:
            if lock.acquire(blocking=True):
                return lock
            return None
        except LockError:
            # Redis reachable at connect time but failed mid-operation - degrade to the
            # in-process fallback for this call rather than let a request hang/500.
            logger.warning("Chat lock: Redis lock.acquire failed for key=%s, degrading to in-process lock for this request", key)

    with _fallback_locks_guard:
        if key not in _fallback_locks:
            _fallback_locks[key] = threading.Lock()
        py_lock = _fallback_locks[key]
    if py_lock.acquire(blocking=True, timeout=timeout_seconds):
        return _FallbackLockHandle(py_lock)
    return None


def release_chat_lock(handle: Any) -> None:
    """Best-effort release - never raises, so a release-time hiccup can never mask or replace
    the turn's actual response."""
    if handle is None:
        return
    try:
        handle.release()
    except LockNotOwnedError:
        # Our own auto-expire TTL fired before we got here (turn ran longer than
        # _LOCK_AUTO_EXPIRE_SECONDS) - the lock is already gone/reassigned, nothing to do.
        logger.warning("Chat lock: release attempted after auto-expiry (turn exceeded %ss)", _LOCK_AUTO_EXPIRE_SECONDS)
    except Exception as exc:
        logger.debug("Chat lock: release failed (non-fatal): %s", exc)
