"""Record student questions, frequency / critical flags, teacher notifications."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.database_models import Lesson as DBLesson
from app.models.phase1_models import Notification
from app.models.phase3_models import StudentLearningQuestion
from app.services.phase3.classify_service import classify_question
from app.services.phase3.learning_event_service import emit_learning_event
from app.services.phase3.phase3_constants import CRITICAL_FREQ_THRESHOLD, CRITICAL_FREQ_WINDOW_DAYS

logger = logging.getLogger(__name__)


def _fingerprint(text: str) -> str:
    norm = " ".join((text or "").lower().split())[:2000]
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def record_student_question(
    db: Session,
    *,
    student_user_id: int,
    lesson_id: Optional[int],
    question_text: str,
    mode: str,
    source_context: Optional[Dict[str, Any]] = None,
    lesson_chat_history_id: Optional[int] = None,
    llm_api_key: Optional[str] = None,
    session_key: Optional[str] = None,
) -> StudentLearningQuestion:
    if mode not in ("lecture", "self_study"):
        mode = "lecture"

    fp = _fingerprint(question_text)
    label, conf, meta = classify_question(question_text, llm_api_key)

    since = datetime.utcnow() - timedelta(days=CRITICAL_FREQ_WINDOW_DAYS)
    existing_same_fp = 0
    if lesson_id:
        existing_same_fp = (
            db.query(func.count(StudentLearningQuestion.id))
            .filter(
                StudentLearningQuestion.lesson_id == lesson_id,
                StudentLearningQuestion.canonical_fingerprint == fp,
                StudentLearningQuestion.created_at >= since,
            )
            .scalar()
            or 0
        )

    total_after = int(existing_same_fp) + 1
    crit = label == "misconception" or (lesson_id is not None and total_after >= CRITICAL_FREQ_THRESHOLD)

    row = StudentLearningQuestion(
        student_user_id=student_user_id,
        lesson_id=lesson_id,
        mode=mode,
        question_text=question_text[:16000],
        source_context_json=json.dumps(source_context or {}, default=str) if source_context else None,
        lesson_chat_history_id=lesson_chat_history_id,
        understanding_label=label,
        understanding_confidence=conf,
        understanding_meta_json=json.dumps(meta, default=str),
        canonical_fingerprint=fp,
        is_critical=crit,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    emit_learning_event(
        db,
        event_type="student.question.asked",
        payload={
            "question_id": row.id,
            "mode": mode,
            "label": label,
            "critical": crit,
        },
        student_user_id=student_user_id,
        lesson_id=lesson_id,
        session_key=session_key,
        sync_only=True,
    )

    if crit and lesson_id:
        _maybe_notify_teacher(db, lesson_id=lesson_id, question_text=question_text, question_id=row.id)

    return row


def _maybe_notify_teacher(db: Session, *, lesson_id: int, question_text: str, question_id: int) -> None:
    lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    if not lesson:
        return
    tid = int(lesson.teacher_id)
    from sqlalchemy import desc

    recent_same = (
        db.query(Notification)
        .filter(
            Notification.recipient_id == tid,
            Notification.title == "Critical student question pattern",
            Notification.created_at >= datetime.utcnow() - timedelta(hours=6),
        )
        .order_by(desc(Notification.created_at))
        .first()
    )
    if recent_same and f"Lesson #{lesson_id}" in (recent_same.message or ""):
        return

    db.add(
        Notification(
            recipient_id=tid,
            title="Critical student question pattern",
            message=f"Lesson #{lesson_id}: students flagged high-priority questions. Latest: {question_text[:200]}",
            type="action",
            action_link="/next-day-review",
        )
    )
    db.commit()
