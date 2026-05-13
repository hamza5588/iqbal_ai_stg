"""Publish learning events to Redis for downstream subscribers (analytics, streaming)."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


def publish_learning_event_to_redis(payload: Dict[str, Any]) -> bool:
    """
    Publishes one JSON message to Redis channel `phase3:learning_events`.
    Uses CELERY_BROKER_URL when it is a redis:// URL.
    """
    if os.getenv("PHASE3_EVENTS_FANOUT_REDIS", "true").lower() not in ("1", "true", "yes"):
        return False
    broker = os.getenv("CELERY_BROKER_URL", "")
    if not broker.startswith("redis://"):
        return False
    try:
        import redis

        r = redis.from_url(broker, decode_responses=True)
        r.publish("phase3:learning_events", json.dumps(payload, default=str))
        return True
    except Exception as exc:
        logger.debug("Redis learning-event publish skipped: %s", exc)
        return False
