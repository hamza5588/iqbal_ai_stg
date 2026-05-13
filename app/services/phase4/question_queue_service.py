"""Unified question delivery queue with priority ordering."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.phase4_models import QUEUE_SOURCE_PRIORITY, StudentQuestionQueueItem


def _priority_tuple(row: StudentQuestionQueueItem) -> tuple:
    src = (row.source or "").lower()
    pr = QUEUE_SOURCE_PRIORITY.get(src, 99)
    due = row.due_at or datetime.max
    return (pr, due, row.id)


def enqueue(
    db: Session,
    *,
    student_user_id: int,
    question_bank_item_id: int,
    source: str,
    due_at: Optional[datetime] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> StudentQuestionQueueItem:
    row = StudentQuestionQueueItem(
        student_user_id=int(student_user_id),
        question_bank_item_id=int(question_bank_item_id),
        source=source,
        due_at=due_at,
        status="pending",
        payload_json=json.dumps(payload or {}, default=str),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_next(
    db: Session,
    *,
    student_user_id: int,
    limit: int = 20,
    status: str = "pending",
) -> List[StudentQuestionQueueItem]:
    rows = (
        db.query(StudentQuestionQueueItem)
        .filter(
            StudentQuestionQueueItem.student_user_id == int(student_user_id),
            StudentQuestionQueueItem.status == status,
        )
        .all()
    )
    rows.sort(key=_priority_tuple)
    return rows[:limit]


def mark_status(db: Session, *, item_id: int, student_user_id: int, status: str) -> bool:
    row = (
        db.query(StudentQuestionQueueItem)
        .filter(
            StudentQuestionQueueItem.id == item_id,
            StudentQuestionQueueItem.student_user_id == int(student_user_id),
        )
        .first()
    )
    if not row:
        return False
    row.status = status
    db.commit()
    return True


def dequeue_next_pending(db: Session, *, student_user_id: int) -> Optional[StudentQuestionQueueItem]:
    nxt = list_next(db, student_user_id=student_user_id, limit=1)
    if not nxt:
        return None
    row = nxt[0]
    row.status = "dispatched"
    db.commit()
    db.refresh(row)
    return row
