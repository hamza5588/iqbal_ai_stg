"""Append-only learning events + optional Celery fan-out."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.phase3_models import LearningEvent

logger = logging.getLogger(__name__)


def emit_learning_event(
    db: Session,
    *,
    event_type: str,
    payload: Dict[str, Any],
    student_user_id: Optional[int] = None,
    lesson_id: Optional[int] = None,
    session_key: Optional[str] = None,
    sync_only: bool = False,
    skip_redis: bool = False,
) -> LearningEvent:
    row = LearningEvent(
        event_type=event_type,
        student_user_id=student_user_id,
        lesson_id=lesson_id,
        session_key=session_key,
        payload_json=json.dumps(payload, default=str),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    if not skip_redis:
        try:
            from app.services.phase3.learning_event_fanout import publish_learning_event_to_redis

            try:
                body = json.loads(row.payload_json) if row.payload_json else {}
            except Exception:
                body = {}
            publish_learning_event_to_redis(
                {
                    "id": row.id,
                    "event_type": row.event_type,
                    "student_user_id": row.student_user_id,
                    "lesson_id": row.lesson_id,
                    "session_key": row.session_key,
                    "payload": body,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
            )
        except Exception as exc:
            logger.debug("Phase3 Redis fan-out skipped: %s", exc)

    if not sync_only and os.getenv("PHASE3_EVENTS_USE_CELERY", "").lower() in ("1", "true", "yes"):
        try:
            from app.tasks.phase3_tasks import fanout_learning_event_task

            fanout_learning_event_task.delay(row.id)
        except Exception as exc:
            logger.warning("Phase3 Celery fan-out skipped: %s", exc)
    return row


def record_client_events_batch(
    db: Session,
    *,
    student_user_id: int,
    events: list,
) -> int:
    """Browser study meter / interaction batch."""
    n = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        emit_learning_event(
            db,
            event_type=str(ev.get("type") or "client.unknown"),
            payload=ev,
            student_user_id=student_user_id,
            lesson_id=ev.get("lesson_id"),
            session_key=ev.get("session_key"),
            sync_only=True,
            skip_redis=True,
        )
        n += 1
    return n
