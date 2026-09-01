"""Assessment attempt and scoring service."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.models.lms_models import AssessmentAttempt, AttemptAnswer, Question, StudentProfile
from app.services.lms.assessment_service import get_assessment
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.mcq_utils import options_from_json
from app.utils.db import get_db

logger = logging.getLogger(__name__)


def _student_completed_diagnostic(student_id: int, assessment_id: int) -> bool:
    db = get_db()
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == student_id).first()
    if profile and profile.diagnostic_completed:
        return True
    submitted = (
        db.query(AssessmentAttempt)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.assessment_id == assessment_id,
            AssessmentAttempt.status == "submitted",
        )
        .first()
    )
    return submitted is not None


def start_attempt(
    student_id: int,
    assessment_id: int,
    assignment_id: Optional[int] = None,
) -> AssessmentAttempt:
    assessment = get_assessment(assessment_id)
    if assessment.status != "published":
        raise LMSValidationError("Assessment is not published")

    if assessment.assessment_type == "diagnostic":
        if _student_completed_diagnostic(student_id, assessment_id):
            raise LMSValidationError(
                "You have already completed the diagnostic assessment. Retakes are not allowed."
            )

    db = get_db()
    attempt = AssessmentAttempt(
        student_id=student_id,
        assessment_id=assessment_id,
        assignment_id=assignment_id,
        status="in_progress",
        max_score=float(len(assessment.questions)),
    )

    if assessment.assessment_type == "diagnostic":
        from app.services.lms.diagnostic_timer_service import compute_attempt_deadline

        total_seconds = compute_attempt_deadline(assessment_id)
        attempt.expires_at = datetime.utcnow() + timedelta(seconds=total_seconds)

    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def _check_attempt_expired(attempt: AssessmentAttempt) -> None:
    if attempt.expires_at and attempt.status == "in_progress":
        if datetime.utcnow() > attempt.expires_at:
            raise LMSValidationError("Diagnostic time has expired")


def get_attempt(attempt_id: int) -> AssessmentAttempt:
    db = get_db()
    attempt = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id).first()
    if not attempt:
        raise LMSNotFoundError(f"Attempt {attempt_id} not found")
    return attempt


def get_delivery_questions(attempt_id: int) -> List[dict]:
    """Return questions without correct answers for student delivery."""
    attempt = get_attempt(attempt_id)
    _check_attempt_expired(attempt)
    assessment = get_assessment(attempt.assessment_id)
    db = get_db()
    result = []
    for aq in sorted(assessment.questions, key=lambda x: x.sort_order):
        q = db.query(Question).filter(Question.id == aq.question_id).first()
        if not q:
            continue
        opts = options_from_json(q.options_json)
        safe_opts = [{"label": o["label"], "text": o["text"], "latex": o.get("latex")} for o in opts]
        result.append(
            {
                "question_id": q.id,
                "question_text": q.question_text,
                "question_latex": q.question_latex,
                "options": safe_opts,
                "sort_order": aq.sort_order,
                "difficulty": q.difficulty,
                "time_limit_seconds": q.time_limit_seconds,
            }
        )
    return result


def get_attempt_timer_info(attempt_id: int) -> dict:
    """Return timer metadata for an in-progress diagnostic attempt."""
    attempt = get_attempt(attempt_id)
    assessment = get_assessment(attempt.assessment_id)
    remaining = None
    if attempt.expires_at:
        delta = (attempt.expires_at - datetime.utcnow()).total_seconds()
        remaining = max(0, int(delta))
    return {
        "attempt_id": attempt.id,
        "assessment_type": assessment.assessment_type,
        "expires_at": attempt.expires_at.isoformat() if attempt.expires_at else None,
        "remaining_seconds": remaining,
        "time_limit_minutes": assessment.time_limit_minutes,
        "is_expired": remaining == 0 if remaining is not None else False,
    }


def save_answer(attempt_id: int, question_id: int, selected_option_index: int) -> AttemptAnswer:
    attempt = get_attempt(attempt_id)
    if attempt.status != "in_progress":
        raise LMSValidationError("Attempt is not in progress")
    _check_attempt_expired(attempt)

    db = get_db()
    answer = (
        db.query(AttemptAnswer)
        .filter(AttemptAnswer.attempt_id == attempt_id, AttemptAnswer.question_id == question_id)
        .first()
    )
    if not answer:
        answer = AttemptAnswer(attempt_id=attempt_id, question_id=question_id)
        db.add(answer)
    answer.selected_option_index = selected_option_index
    db.commit()
    db.refresh(answer)
    return answer


def submit_attempt(attempt_id: int) -> dict:
    attempt = get_attempt(attempt_id)
    if attempt.status == "submitted":
        return get_attempt_results(attempt_id)
    if attempt.status != "in_progress":
        raise LMSValidationError("Attempt is not in progress")

    assessment = get_assessment(attempt.assessment_id)
    expired = (
        attempt.expires_at
        and datetime.utcnow() > attempt.expires_at
        and assessment.assessment_type == "diagnostic"
    )
    if expired:
        logger.info("Auto-submitting expired diagnostic attempt %s", attempt_id)

    db = get_db()
    question_ids = [aq.question_id for aq in assessment.questions]
    questions = db.query(Question).filter(Question.id.in_(question_ids)).all()
    q_by_id = {q.id: q for q in questions}

    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt_id).all()
    answer_by_q = {a.question_id: a for a in answers}

    correct = 0
    topic_breakdown: Dict[int, dict] = {}

    for qid in question_ids:
        q = q_by_id.get(qid)
        if not q:
            continue
        ans = answer_by_q.get(qid)
        is_correct = (
            ans is not None
            and ans.selected_option_index is not None
            and ans.selected_option_index == q.correct_option_index
        )
        if ans:
            ans.is_correct = is_correct
        if is_correct:
            correct += 1
        if q.topic_id:
            bucket = topic_breakdown.setdefault(
                q.topic_id, {"topic_id": q.topic_id, "correct": 0, "total": 0}
            )
            bucket["total"] += 1
            if is_correct:
                bucket["correct"] += 1

    max_score = float(len(question_ids)) or 1.0
    attempt.score = correct
    attempt.max_score = max_score
    attempt.status = "submitted"
    attempt.submitted_at = datetime.utcnow()
    db.commit()

    from app.services.lms import performance_service, learning_path_service

    def _post_submit_step(fn):
        try:
            fn()
        except Exception as exc:
            logger.warning("Post-submit step failed for attempt %s: %s", attempt_id, exc)
            db.rollback()

    _post_submit_step(lambda: performance_service.update_topic_scores_from_attempt(attempt_id))
    _post_submit_step(lambda: learning_path_service.refresh_learning_path(attempt.student_id))
    _post_submit_step(lambda: performance_service.create_snapshot(attempt.student_id))

    if assessment.assessment_type == "diagnostic":
        from app.services.lms import deficiency_chat_service, student_profile_service

        _post_submit_step(
            lambda: deficiency_chat_service._close_old_sessions(attempt.student_id)
        )
        _post_submit_step(
            lambda: student_profile_service.mark_diagnostic_complete(
                attempt.student_id, assessment_id=assessment.id
            )
        )

    if attempt.assignment_id:
        from app.services.lms import assignment_service

        _post_submit_step(
            lambda: assignment_service.mark_submission_complete(
                attempt.assignment_id, attempt.student_id, attempt_id
            )
        )
    result = {
        "attempt_id": attempt.id,
        "score": correct,
        "max_score": max_score,
        "score_percent": round(100.0 * correct / max_score, 2),
        "topic_breakdown": list(topic_breakdown.values()),
        "assessment_type": assessment.assessment_type,
    }

    if assessment.assessment_type == "diagnostic":
        analysis = performance_service.analyze_attempt(attempt_id)
        result["weak_topics"] = analysis.get("weak_topics", [])
        result["strong_topics"] = analysis.get("strong_topics", [])
        result["diagnostic_completed"] = True

    return result


def get_attempt_results(attempt_id: int) -> dict:
    """Return scored attempt summary (idempotent)."""
    attempt = get_attempt(attempt_id)
    if attempt.status != "submitted":
        raise LMSValidationError("Attempt not yet submitted")
    max_score = attempt.max_score or 1.0
    score = attempt.score or 0.0
    assessment = get_assessment(attempt.assessment_id)
    result = {
        "attempt_id": attempt.id,
        "score": score,
        "max_score": max_score,
        "score_percent": round(100.0 * score / max_score, 2) if max_score else 0.0,
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        "assessment_type": assessment.assessment_type,
    }
    if assessment.assessment_type == "diagnostic":
        from app.services.lms import performance_service

        analysis = performance_service.analyze_attempt(attempt_id)
        result["weak_topics"] = analysis.get("weak_topics", [])
        result["strong_topics"] = analysis.get("strong_topics", [])
        result["diagnostic_completed"] = True
    return result


def list_student_attempts(student_id: int, limit: int = 50) -> List[dict]:
    db = get_db()
    rows = (
        db.query(AssessmentAttempt)
        .filter(AssessmentAttempt.student_id == student_id)
        .order_by(AssessmentAttempt.started_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for a in rows:
        pct = None
        if a.status == "submitted" and a.max_score:
            pct = round(100.0 * (a.score or 0) / a.max_score, 1)
        result.append(
            {
                "attempt_id": a.id,
                "assessment_id": a.assessment_id,
                "assignment_id": a.assignment_id,
                "status": a.status,
                "score_percent": pct,
                "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            }
        )
    return result
