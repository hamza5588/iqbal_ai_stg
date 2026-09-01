"""Cross-process progress for diagnostic PDF upload/processing (admin flow)."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

try:
    import redis
except Exception:  # pragma: no cover
    redis = None

logger = logging.getLogger(__name__)

_redis_client = None
_memory_progress: Dict[str, Dict[str, Any]] = {}
_TTL_SECONDS = 600


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
    except Exception as exc:
        logger.warning("Diagnostic upload progress: Redis unavailable (%s)", exc)
        _redis_client = None
    return _redis_client


def _key(job_id: str) -> str:
    return f"diagnostic_upload_progress:{job_id}"


def set_progress(
    job_id: Optional[str],
    percent: int,
    message: str,
    *,
    stage: str = "",
    done: bool = False,
) -> None:
    if not job_id or not message:
        return
    payload = {
        "percent": max(0, min(100, int(percent))),
        "message": message,
        "stage": stage,
        "done": bool(done),
        "ts": time.time(),
    }
    client = _get_redis_client()
    if client is not None:
        try:
            client.set(_key(job_id), json.dumps(payload), ex=_TTL_SECONDS)
            return
        except Exception as exc:
            logger.debug("Diagnostic upload progress set failed: %s", exc)
    _memory_progress[job_id] = payload


def get_progress(job_id: Optional[str]) -> dict:
    if not job_id:
        return {}
    client = _get_redis_client()
    if client is not None:
        try:
            raw = client.get(_key(job_id))
            if raw:
                data = json.loads(raw)
                return {
                    "percent": data.get("percent", 0),
                    "message": data.get("message", ""),
                    "stage": data.get("stage", ""),
                    "done": bool(data.get("done", False)),
                }
        except Exception as exc:
            logger.debug("Diagnostic upload progress get failed: %s", exc)
    return dict(_memory_progress.get(job_id) or {})


def clear_progress(job_id: Optional[str]) -> None:
    if not job_id:
        return
    client = _get_redis_client()
    if client is not None:
        try:
            client.delete(_key(job_id))
        except Exception:
            pass
    _memory_progress.pop(job_id, None)
