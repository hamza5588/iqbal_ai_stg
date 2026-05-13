"""Micro-revision sessions with rotated explanation styles."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.phase3_models import StudentLearningPreferences
from app.models.phase4_models import MicroRevisionSession

STYLES = (
    "real_world_example",
    "story",
    "exam_question",
    "analogy",
    "visual_explanation",
)


def _next_style(db: Session, *, student_user_id: int) -> str:
    pref = db.query(StudentLearningPreferences).filter_by(student_user_id=student_user_id).first()
    rot: Dict[str, Any] = {}
    if pref and pref.reminder_state_json:
        try:
            blob = json.loads(pref.reminder_state_json)
            rot = blob.get("micro_revision_rotation") or {}
        except Exception:
            rot = {}
    last = rot.get("last_style")
    idx = (STYLES.index(last) + 1) % len(STYLES) if last in STYLES else 0
    style = STYLES[idx]
    if pref:
        try:
            blob = json.loads(pref.reminder_state_json or "{}")
        except Exception:
            blob = {}
        blob["micro_revision_rotation"] = {"last_style": style}
        pref.reminder_state_json = json.dumps(blob)
        db.add(pref)
        db.flush()
    return style


def start_micro_revision(
    db: Session,
    *,
    student_user_id: int,
    syllabus_topic_id: Optional[int] = None,
    recall_seconds: int = 30,
) -> MicroRevisionSession:
    style = _next_style(db, student_user_id=student_user_id)
    now = datetime.utcnow()
    row = MicroRevisionSession(
        student_user_id=int(student_user_id),
        syllabus_topic_id=syllabus_topic_id,
        style=style,
        recall_deadline_at=now + timedelta(seconds=recall_seconds),
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def submit_recall(
    db: Session,
    *,
    session_id: int,
    student_user_id: int,
    recall_text: str,
) -> MicroRevisionSession:
    row = (
        db.query(MicroRevisionSession)
        .filter(
            MicroRevisionSession.id == session_id,
            MicroRevisionSession.student_user_id == int(student_user_id),
        )
        .first()
    )
    if not row:
        raise ValueError("not_found")
    now = datetime.utcnow()
    row.recall_response_text = recall_text
    if row.recall_deadline_at and now > row.recall_deadline_at and not recall_text.strip():
        row.status = "expired"
    else:
        row.recall_feedback_json = json.dumps(
            {
                "submitted_at": now.isoformat(),
                "auto_closed": bool(row.recall_deadline_at and now > row.recall_deadline_at),
                "hint": "Compare your recall with your notes or the syllabus topic title.",
            },
            default=str,
        )
        row.status = "completed"
    db.commit()
    db.refresh(row)
    return row


def session_to_dict(row: MicroRevisionSession) -> Dict[str, Any]:
    return {
        "id": row.id,
        "style": row.style,
        "recall_deadline_at": row.recall_deadline_at.isoformat() if row.recall_deadline_at else None,
        "status": row.status,
        "recall_feedback": json.loads(row.recall_feedback_json) if row.recall_feedback_json else None,
    }
