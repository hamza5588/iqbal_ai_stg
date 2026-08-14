"""
Lightweight cross-process "what is the AI doing right now" progress tracker for RAG chat turns.

Redis-backed (same broker URL convention as the Lesson QA rate limiter in lesson_routes.py) so
a poll request landing on any gunicorn worker can see updates written by whichever worker is
actually running the turn. Best-effort only: if Redis is unavailable, every call here is a no-op
and the frontend just keeps showing its static "AI is thinking..." indicator - progress display
is a UX nicety, never a dependency for the chat turn itself.
"""
import json
import logging
import os
import time
from urllib.parse import urlparse

try:
    import redis
except Exception:  # pragma: no cover - optional dependency fallback
    redis = None

logger = logging.getLogger(__name__)

_redis_client = None
_TTL_SECONDS = 120


def _get_redis_client():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if redis is None:
        return None
    redis_url = (
        os.getenv("CHAT_PROGRESS_REDIS_URL")
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
        logger.info("Chat progress tracker: using Redis backend at %s", redis_url)
    except Exception as exc:
        logger.warning(
            "Chat progress tracker: Redis unavailable (%s); progress updates disabled.", exc
        )
        _redis_client = None
    return _redis_client


def _key(thread_id: str) -> str:
    return f"chat_progress:{thread_id}"


def set_progress(thread_id: str, message: str) -> None:
    """Publish the current step for a chat turn. Best-effort: never raises."""
    if not thread_id or not message:
        return
    client = _get_redis_client()
    if client is None:
        return
    try:
        client.set(
            _key(thread_id),
            json.dumps({"message": message, "ts": time.time()}),
            ex=_TTL_SECONDS,
        )
    except Exception as exc:
        logger.debug("Chat progress set failed for thread_id=%s: %s", thread_id, exc)


def get_progress(thread_id: str) -> dict:
    """Returns {"message": str} for the most recent step, or {} if none/unavailable."""
    if not thread_id:
        return {}
    client = _get_redis_client()
    if client is None:
        return {}
    try:
        raw = client.get(_key(thread_id))
        if not raw:
            return {}
        data = json.loads(raw)
        return {"message": data.get("message", "")}
    except Exception as exc:
        logger.debug("Chat progress get failed for thread_id=%s: %s", thread_id, exc)
        return {}


def clear_progress(thread_id: str) -> None:
    """Best-effort cleanup once a turn's final answer has been produced."""
    if not thread_id:
        return
    client = _get_redis_client()
    if client is None:
        return
    try:
        client.delete(_key(thread_id))
    except Exception:
        pass
