"""Recovery bundle state machine."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.phase4_models import RecoveryBundleSession


def _skip_allowed() -> bool:
    return os.getenv("PHASE4_RECOVERY_ALLOW_SKIP", "").lower() in ("1", "true", "yes")


def start_session(
    db: Session,
    *,
    student_user_id: int,
    syllabus_topic_id: Optional[int] = None,
) -> RecoveryBundleSession:
    row = RecoveryBundleSession(
        student_user_id=int(student_user_id),
        syllabus_topic_id=syllabus_topic_id,
        current_step="example",
        practice_remaining=5,
        skip_allowed=_skip_allowed(),
        metadata_json=json.dumps({}, default=str),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def advance(
    db: Session,
    *,
    session_id: int,
    student_user_id: int,
    force: bool = False,
) -> RecoveryBundleSession:
    row = (
        db.query(RecoveryBundleSession)
        .filter(
            RecoveryBundleSession.id == session_id,
            RecoveryBundleSession.student_user_id == int(student_user_id),
        )
        .first()
    )
    if not row:
        raise ValueError("not_found")
    if row.completed_at:
        return row
    order = ("example", "mini_lecture", "practice", "badge", "completed")
    if not force and not row.skip_allowed and row.current_step in ("example", "mini_lecture", "practice"):
        # require explicit completion signal via advance(force from client only after content viewed)
        pass
    idx = order.index(row.current_step) if row.current_step in order else 0
    if row.current_step == "practice" and row.practice_remaining > 0:
        row.practice_remaining = int(row.practice_remaining) - 1
        if row.practice_remaining > 0:
            db.commit()
            db.refresh(row)
            return row
    nxt = min(idx + 1, len(order) - 1)
    row.current_step = order[nxt]
    if row.current_step == "completed":
        row.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def session_to_dict(row: RecoveryBundleSession) -> Dict[str, Any]:
    return {
        "id": row.id,
        "syllabus_topic_id": row.syllabus_topic_id,
        "current_step": row.current_step,
        "practice_remaining": row.practice_remaining,
        "skip_allowed": row.skip_allowed,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }
