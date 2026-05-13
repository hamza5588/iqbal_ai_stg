"""Question practice attempts: timer, confidence, guess flag, mistake aggregates."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.phase3_models import QuestionBankItem
from app.models.phase4_models import QuestionPracticeAttempt, StudentConceptSchedule, StudentMistakeCounter
from app.services.phase3.learning_event_service import emit_learning_event
from app.services.phase4 import error_classification_service, guess_detection


def _concept_key_from_item(item: Optional[QuestionBankItem]) -> str:
    if not item:
        return "unknown"
    tags: List[str] = []
    if item.tags_json:
        try:
            tags = list(json.loads(item.tags_json))
        except Exception:
            tags = []
    tid = item.syllabus_topic_id or 0
    tag_part = ",".join(sorted(str(t) for t in tags[:5]))
    return f"t{tid}:{tag_part}"


def start_attempt(
    db: Session,
    *,
    student_user_id: int,
    question_bank_item_id: int,
    queue_item_id: Optional[int] = None,
) -> QuestionPracticeAttempt:
    now = datetime.utcnow()
    row = QuestionPracticeAttempt(
        student_user_id=int(student_user_id),
        question_bank_item_id=int(question_bank_item_id),
        queue_item_id=queue_item_id,
        started_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    emit_learning_event(
        db,
        event_type="practice.question_started",
        payload={"attempt_id": row.id, "question_bank_item_id": question_bank_item_id},
        student_user_id=int(student_user_id),
        sync_only=True,
        skip_redis=True,
    )
    return row


def submit_attempt(
    db: Session,
    *,
    attempt_id: int,
    student_user_id: int,
    confidence_before_result: int,
    response_payload: Dict[str, Any],
    is_correct: bool,
    correct_answer_hint: str = "",
    recent_pattern: Optional[List[bool]] = None,
) -> QuestionPracticeAttempt:
    row = (
        db.query(QuestionPracticeAttempt)
        .filter(
            QuestionPracticeAttempt.id == attempt_id,
            QuestionPracticeAttempt.student_user_id == int(student_user_id),
        )
        .first()
    )
    if not row:
        raise ValueError("attempt_not_found")
    if row.answered_at is not None:
        raise ValueError("attempt_already_submitted")
    if confidence_before_result < 1 or confidence_before_result > 5:
        raise ValueError("invalid_confidence")

    now = datetime.utcnow()
    duration_ms = int((now - row.started_at).total_seconds() * 1000)
    item = (
        db.query(QuestionBankItem).filter(QuestionBankItem.id == row.question_bank_item_id).first()
        if row.question_bank_item_id
        else None
    )
    diff = int(item.difficulty) if item else 3
    is_guess, sig = guess_detection.detect_guess(
        duration_ms=duration_ms,
        difficulty=diff,
        is_correct=is_correct,
        recent_answer_pattern=recent_pattern,
    )
    exclude = bool(is_guess)

    err_type = None
    err_expl = None
    if not is_correct:
        stem = item.stem if item else ""
        chosen = str(response_payload.get("selected") or response_payload.get("answer") or "")
        err_type, err_expl, _meta = error_classification_service.classify_wrong_answer(
            stem=stem,
            chosen_answer=chosen,
            correct_answer=correct_answer_hint or str(response_payload.get("correct") or ""),
            duration_ms=duration_ms,
            confidence_1_5=confidence_before_result,
        )

    row.answered_at = now
    row.duration_ms = duration_ms
    row.confidence_before_result = confidence_before_result
    row.response_payload_json = json.dumps(response_payload, default=str)
    row.is_correct = is_correct
    row.is_guess = is_guess
    row.guess_signals_json = json.dumps(sig, default=str)
    row.exclude_from_pass_probability = exclude
    row.error_type = err_type
    row.error_explanation = err_expl

    if not is_correct:
        _bump_mistake(
            db,
            student_user_id=int(student_user_id),
            syllabus_topic_id=item.syllabus_topic_id if item else None,
            concept_key=_concept_key_from_item(item),
            error_type=err_type,
        )

    _update_concept_schedule(db, item=item, student_user_id=int(student_user_id), is_correct=is_correct)

    db.commit()
    db.refresh(row)

    emit_learning_event(
        db,
        event_type="practice.question_answered",
        payload={
            "attempt_id": row.id,
            "question_bank_item_id": row.question_bank_item_id,
            "is_correct": is_correct,
            "duration_ms": duration_ms,
            "confidence": confidence_before_result,
            "is_guess": is_guess,
            "error_type": err_type,
        },
        student_user_id=int(student_user_id),
        sync_only=True,
        skip_redis=True,
    )
    return row


def _update_concept_schedule(
    db: Session, *, item: Optional[QuestionBankItem], student_user_id: int, is_correct: bool
) -> None:
    if not item:
        return
    ck = _concept_key_from_item(item)
    row = (
        db.query(StudentConceptSchedule)
        .filter(
            StudentConceptSchedule.student_user_id == student_user_id,
            StudentConceptSchedule.concept_key == ck,
        )
        .first()
    )
    if not row:
        row = StudentConceptSchedule(
            student_user_id=student_user_id,
            concept_key=ck,
            syllabus_topic_id=item.syllabus_topic_id,
            ease_factor=2.5,
            interval_days=1,
            repetitions=0,
        )
        db.add(row)
    if is_correct:
        row.repetitions = int(row.repetitions or 0) + 1
        row.interval_days = max(
            1, int(round(int(row.interval_days or 1) * float(row.ease_factor or 2.5) * 0.35))
        )
        row.ease_factor = min(3.0, float(row.ease_factor or 2.5) + 0.05)
    else:
        row.repetitions = 0
        row.interval_days = 1
        row.ease_factor = max(1.3, float(row.ease_factor or 2.5) - 0.2)
    row.last_reviewed_at = datetime.utcnow()
    row.next_review_at = datetime.utcnow() + timedelta(days=int(row.interval_days))
    row.strength_estimate = 0.62 if is_correct else 0.38
    db.flush()


def _bump_mistake(
    db: Session,
    *,
    student_user_id: int,
    syllabus_topic_id: Optional[int],
    concept_key: str,
    error_type: Optional[str],
) -> None:
    q = db.query(StudentMistakeCounter).filter(
        StudentMistakeCounter.student_user_id == student_user_id,
        StudentMistakeCounter.syllabus_topic_id == syllabus_topic_id,
        StudentMistakeCounter.concept_key == concept_key,
        StudentMistakeCounter.error_type == error_type,
    )
    row = q.first()
    if not row:
        row = StudentMistakeCounter(
            student_user_id=student_user_id,
            syllabus_topic_id=syllabus_topic_id,
            concept_key=concept_key,
            error_type=error_type,
            mistake_count=1,
            last_occurred_at=datetime.utcnow(),
        )
        db.add(row)
    else:
        row.mistake_count = int(row.mistake_count or 0) + 1
        row.last_occurred_at = datetime.utcnow()
    db.flush()
    if int(row.mistake_count or 0) == 3:
        try:
            from app.services.notification_service import create_notification

            create_notification(
                db,
                recipient_id=student_user_id,
                title="Repeated mistake pattern",
                message=(
                    "The same type of error has shown up several times. "
                    "Try a recovery bundle or ask your teacher for a quick check-in."
                ),
                type="phase4_mistake_alert",
                action_link="/student-learning/phase4-intelligence",
            )
        except Exception:
            pass


def attach_similar_followup(
    db: Session,
    *,
    attempt_id: int,
    student_user_id: int,
    similar_item_id: int,
) -> None:
    row = (
        db.query(QuestionPracticeAttempt)
        .filter(
            QuestionPracticeAttempt.id == attempt_id,
            QuestionPracticeAttempt.student_user_id == int(student_user_id),
        )
        .first()
    )
    if row:
        row.similar_followup_item_id = int(similar_item_id)
        db.commit()


def recent_correctness_pattern(db: Session, *, student_user_id: int, limit: int = 12) -> List[bool]:
    rows = (
        db.query(QuestionPracticeAttempt.is_correct)
        .filter(
            QuestionPracticeAttempt.student_user_id == int(student_user_id),
            QuestionPracticeAttempt.answered_at.isnot(None),
            QuestionPracticeAttempt.is_correct.isnot(None),
        )
        .order_by(QuestionPracticeAttempt.answered_at.desc())
        .limit(limit)
        .all()
    )
    return [bool(r[0]) for r in rows]


def attempt_to_dict(row: QuestionPracticeAttempt) -> Dict[str, Any]:
    return {
        "id": row.id,
        "question_bank_item_id": row.question_bank_item_id,
        "queue_item_id": row.queue_item_id,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "answered_at": row.answered_at.isoformat() if row.answered_at else None,
        "duration_ms": row.duration_ms,
        "confidence_before_result": row.confidence_before_result,
        "is_correct": row.is_correct,
        "is_guess": row.is_guess,
        "exclude_from_pass_probability": row.exclude_from_pass_probability,
        "error_type": row.error_type,
        "error_explanation": row.error_explanation,
        "similar_followup_item_id": row.similar_followup_item_id,
    }
