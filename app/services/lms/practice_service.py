"""Guided practice sessions (Phase 5)."""
from __future__ import annotations

from typing import Optional

from app.models.lms_models import PracticeAttempt, PracticeSession, Question
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.mcq_utils import options_from_json
from app.services.lms.tutor_service import get_hint
from app.utils.db import get_db


def get_active_session(student_id: int, topic_id: Optional[int] = None) -> Optional[PracticeSession]:
    db = get_db()
    q = db.query(PracticeSession).filter(
        PracticeSession.student_id == student_id,
        PracticeSession.status == "active",
    )
    if topic_id is not None:
        q = q.filter(PracticeSession.topic_id == topic_id)
    return q.order_by(PracticeSession.updated_at.desc()).first()


def _diagnostic_question_for_topic(student_id: int, topic_id: int) -> Optional[int]:
    """A question for a topic that exists only as a diagnostic AI grouping.

    Diagnostic topics are named by the AI grouping and have no question-bank
    rows (questions.topic_id is empty), so practice on them used to fail with
    "No practice question available". Use the student's own diagnostic
    questions from that topic instead - ones they got wrong first.
    """
    from app.models.lms_models import Assessment, AssessmentAttempt, AttemptAnswer
    from app.services.lms.topic_resolver import get_or_create_topic_from_pdf_label
    from app.services.lms.weakness_analyzer import analyze_diagnostic_attempt

    db = get_db()
    attempts = (
        db.query(AssessmentAttempt)
        .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.status == "submitted",
            Assessment.assessment_type == "diagnostic",
        )
        .order_by(AssessmentAttempt.submitted_at.desc())
        .limit(3)
        .all()
    )
    for attempt in attempts:
        for area in analyze_diagnostic_attempt(attempt.id).get("all_topics") or []:
            area_tid = int(area.get("topic_id") or 0)
            if area_tid != int(topic_id):
                named = get_or_create_topic_from_pdf_label(area.get("topic_name") or "")
                if not named or named.id != int(topic_id):
                    continue
            qids = [int(q) for q in area.get("question_ids") or []]
            if not qids:
                continue
            wrong = {
                a.question_id
                for a in db.query(AttemptAnswer).filter(
                    AttemptAnswer.attempt_id == attempt.id, AttemptAnswer.question_id.in_(qids)
                )
                if not a.is_correct
            }
            for qid in [q for q in qids if q in wrong] + [q for q in qids if q not in wrong]:
                q = db.query(Question).filter(Question.id == qid, Question.is_active.is_(True)).first()
                if q:
                    return q.id
    return None


def start_session(
    student_id: int,
    topic_id: Optional[int] = None,
    question_id: Optional[int] = None,
    force_new: bool = False,
) -> tuple[PracticeSession, bool]:
    if not force_new:
        existing = get_active_session(student_id, topic_id)
        if existing:
            return existing, True

    db = get_db()
    if not question_id and topic_id is None:
        # The dashboard's global Practice button has no topic argument. Prefer
        # the student's weakest topics, then fall back to any active question so
        # the visible control always starts a usable session.
        from app.services.lms.performance_service import get_weak_topics_for_student

        weak_topic_ids = [
            item.get("topic_id") for item in get_weak_topics_for_student(student_id)
            if item.get("topic_id") is not None
        ]
        for weak_topic_id in weak_topic_ids:
            q = (
                db.query(Question)
                .filter(Question.topic_id == weak_topic_id, Question.is_active.is_(True))
                .order_by(Question.id.desc())
                .first()
            )
            if q:
                topic_id = weak_topic_id
                question_id = q.id
                break
        if not question_id:
            q = db.query(Question).filter(Question.is_active.is_(True)).order_by(Question.id.desc()).first()
            if q:
                topic_id = q.topic_id
                question_id = q.id
    if not question_id and topic_id:
        q = (
            db.query(Question)
            .filter(Question.topic_id == topic_id, Question.is_active.is_(True))
            .order_by(Question.id.desc())
            .first()
        )
        question_id = q.id if q else None
        if not question_id:
            question_id = _diagnostic_question_for_topic(student_id, topic_id)
    if not question_id:
        raise LMSValidationError("No practice question available for this topic")

    session = PracticeSession(
        student_id=student_id,
        topic_id=topic_id,
        question_id=question_id,
        status="active",
        hint_level=0,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, False


def get_session(session_id: int, student_id: Optional[int] = None) -> PracticeSession:
    db = get_db()
    q = db.query(PracticeSession).filter(PracticeSession.id == session_id)
    if student_id is not None:
        q = q.filter(PracticeSession.student_id == student_id)
    s = q.first()
    if not s:
        raise LMSNotFoundError(f"Practice session {session_id} not found")
    return s


def get_session_question(session_id: int, student_id: Optional[int] = None) -> dict:
    session = get_session(session_id, student_id)
    db = get_db()
    q = db.query(Question).filter(Question.id == session.question_id).first()
    if not q:
        raise LMSNotFoundError("Question not found")
    opts = options_from_json(q.options_json)
    safe = [{"label": o["label"], "text": o["text"]} for o in opts]
    return {
        "session_id": session.id,
        "question_id": q.id,
        "question_text": q.question_text,
        "options": safe,
        "hint_level": session.hint_level,
    }


def submit_answer(
    session_id: int, selected_option_index: int, student_id: Optional[int] = None
) -> dict:
    db = get_db()
    session = get_session(session_id, student_id)
    if session.status != "active":
        raise LMSValidationError("Session not active")
    q = db.query(Question).filter(Question.id == session.question_id).first()
    if not q:
        raise LMSNotFoundError("Question not found")
    is_correct = selected_option_index == q.correct_option_index
    db.add(
        PracticeAttempt(
            session_id=session_id,
            selected_option_index=selected_option_index,
            is_correct=is_correct,
            hint_level_used=session.hint_level,
        )
    )
    if is_correct:
        session.status = "completed"
    db.commit()
    return {
        "correct": is_correct,
        "session_status": session.status,
        "explanation": q.explanation if is_correct else None,
    }


def request_hint(session_id: int, student_id: Optional[int] = None) -> dict:
    db = get_db()
    session = get_session(session_id, student_id)
    session.hint_level = min(session.hint_level + 1, 2)
    db.commit()
    q = db.query(Question).filter(Question.id == session.question_id).first()
    hint = get_hint(session.hint_level, q.question_text if q else None)
    return {"hint_level": session.hint_level, "hint": hint}
