"""Assessment attempt and scoring service."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.models.lms_models import AssessmentAttempt, AttemptAnswer, Question, StudentProfile
from app.services.lms.assessment_service import get_assessment
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.mcq_utils import options_from_json, pick_display_fields
from app.utils.db import get_db

logger = logging.getLogger(__name__)

TIME_OVER_MESSAGE = (
    "Time is up. Your diagnostic was submitted automatically — "
    "every question you answered has been scored."
)


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


def _student_completed_quiz(student_id: int, assessment_id: int) -> bool:
    """True if the student already submitted this quiz (no retakes)."""
    db = get_db()
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


def _find_in_progress_attempt(
    student_id: int,
    assessment_id: int,
    assignment_id: Optional[int] = None,
) -> Optional[AssessmentAttempt]:
    db = get_db()
    q = (
        db.query(AssessmentAttempt)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.assessment_id == assessment_id,
            AssessmentAttempt.status == "in_progress",
        )
        .order_by(AssessmentAttempt.started_at.desc())
    )
    if assignment_id is not None:
        q = q.filter(AssessmentAttempt.assignment_id == assignment_id)
    return q.first()


def _abandon_stale_in_progress_attempts(
    student_id: int,
    assessment_id: int,
    keep_attempt_id: int,
    assignment_id: Optional[int] = None,
) -> None:
    """Mark duplicate in-progress attempts as abandoned (legacy data cleanup)."""
    db = get_db()
    q = db.query(AssessmentAttempt).filter(
        AssessmentAttempt.student_id == student_id,
        AssessmentAttempt.assessment_id == assessment_id,
        AssessmentAttempt.status == "in_progress",
        AssessmentAttempt.id != keep_attempt_id,
    )
    if assignment_id is not None:
        q = q.filter(AssessmentAttempt.assignment_id == assignment_id)
    for row in q.all():
        row.status = "abandoned"
    db.commit()


def _attempt_is_expired(attempt: AssessmentAttempt) -> bool:
    return bool(
        attempt.expires_at
        and attempt.status == "in_progress"
        and datetime.utcnow() >= attempt.expires_at
    )


def get_latest_submitted_attempt(
    student_id: int, assessment_id: int
) -> Optional[AssessmentAttempt]:
    db = get_db()
    return (
        db.query(AssessmentAttempt)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.assessment_id == assessment_id,
            AssessmentAttempt.status == "submitted",
        )
        .order_by(AssessmentAttempt.submitted_at.desc(), AssessmentAttempt.id.desc())
        .first()
    )


def finalize_expired_attempt(attempt_id: int) -> dict:
    """Close an attempt whose timer ran out.

    The questions the student already answered are scored normally - only
    the ones they never got to are missing (and simply aren't correct).
    Losing a student's completed work on a timeout was the #1 complaint
    from the DIL rollout.
    """
    attempt = get_attempt(attempt_id)
    if attempt.status == "submitted":
        return get_attempt_results(attempt_id)
    if attempt.status != "in_progress":
        raise LMSValidationError("Attempt is not in progress")

    assessment = get_assessment(attempt.assessment_id)
    logger.info("Finalizing expired diagnostic attempt %s (scoring answered questions)", attempt_id)
    return _score_and_finalize(attempt, assessment, timed_out=True)


def finalize_expired_diagnostic_if_needed(
    student_id: int, assessment_id: int, assignment_id: Optional[int] = None
) -> Optional[dict]:
    existing = _find_in_progress_attempt(student_id, assessment_id, assignment_id)
    if existing and _attempt_is_expired(existing):
        return finalize_expired_attempt(existing.id)
    return None


def start_attempt(
    student_id: int,
    assessment_id: int,
    assignment_id: Optional[int] = None,
) -> tuple[AssessmentAttempt, bool]:
    """Start or resume an attempt. Returns (attempt, resumed)."""
    assessment = get_assessment(assessment_id)
    if assessment.status != "published":
        raise LMSValidationError("Assessment is not published")

    if assessment.assessment_type == "diagnostic":
        from app.services.lms.assessment_service import get_active_platform_diagnostic

        platform = get_active_platform_diagnostic()
        if not platform or platform.id != assessment_id:
            raise LMSValidationError("This diagnostic is not available")
        assignment_id = None
        timeout = finalize_expired_diagnostic_if_needed(student_id, assessment_id)
        if timeout:
            return get_attempt(timeout["attempt_id"]), False
        if _student_completed_diagnostic(student_id, assessment_id):
            latest = get_latest_submitted_attempt(student_id, assessment_id)
            if latest and getattr(latest, "timed_out", False):
                return latest, False
            raise LMSValidationError(
                "You have already completed the diagnostic assessment. Retakes are not allowed."
            )
    elif assessment.assessment_type == "quiz":
        from app.services.lms.assignment_service import resolve_student_quiz_assignment

        assignment_id = resolve_student_quiz_assignment(
            student_id, assessment_id, assignment_id
        )
    else:
        raise LMSValidationError("Unsupported assessment type")

    db = get_db()
    existing = _find_in_progress_attempt(student_id, assessment_id, assignment_id)
    if existing:
        _abandon_stale_in_progress_attempts(
            student_id, assessment_id, existing.id, assignment_id
        )
        return existing, True

    if assessment.assessment_type == "quiz" and _student_completed_quiz(student_id, assessment_id):
        raise LMSValidationError(
            "You have already completed this quiz. Retakes are not allowed."
        )

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
    return attempt, False


def _check_attempt_expired(attempt: AssessmentAttempt) -> None:
    if _attempt_is_expired(attempt):
        finalize_expired_attempt(attempt.id)
        raise LMSValidationError(TIME_OVER_MESSAGE)


def get_attempt(attempt_id: int) -> AssessmentAttempt:
    db = get_db()
    attempt = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id).first()
    if not attempt:
        raise LMSNotFoundError(f"Attempt {attempt_id} not found")
    return attempt


def get_attempt_delivery_state(attempt_id: int) -> dict:
    """Questions plus saved answers and resume position for the student UI."""
    attempt = get_attempt(attempt_id)
    if attempt.status != "in_progress":
        raise LMSValidationError("Attempt is not in progress")
    _check_attempt_expired(attempt)

    questions = get_delivery_questions(attempt_id)
    db = get_db()
    rows = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt_id).all()
    saved_by_qid = {
        a.question_id: a.selected_option_index
        for a in rows
        if a.selected_option_index is not None
    }

    saved_answers: dict[int, int] = {}
    current_index = 0
    for i, q in enumerate(questions):
        qid = q["question_id"]
        if qid in saved_by_qid:
            saved_answers[i] = saved_by_qid[qid]
        else:
            current_index = i
            break
    else:
        if questions:
            current_index = max(0, len(questions) - 1)

    return {
        "attempt_id": attempt_id,
        "questions": questions,
        "saved_answers": saved_answers,
        "current_question_index": current_index,
    }


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
        q_text, q_latex = pick_display_fields(q.question_text, q.question_latex)
        safe_opts = []
        for o in opts:
            text, latex = pick_display_fields(o.get("text"), o.get("latex"))
            safe_opts.append({"label": o.get("label"), "text": text, "latex": latex})
        result.append(
            {
                "question_id": q.id,
                "question_text": q_text or q.question_text,
                "question_latex": q_latex,
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

    time_limit_minutes = assessment.time_limit_minutes
    if assessment.assessment_type == "diagnostic":
        # Keep the displayed limit consistent with the real deadline
        # (which now has a 30-minute floor - see diagnostic_timer_service).
        from app.services.lms.diagnostic_timer_service import compute_attempt_deadline

        try:
            time_limit_minutes = max(1, round(compute_attempt_deadline(assessment.id) / 60))
        except Exception:  # noqa: BLE001 - fall back to the stored value
            pass

    return {
        "attempt_id": attempt.id,
        "assessment_type": assessment.assessment_type,
        "expires_at": attempt.expires_at.isoformat() + "Z" if attempt.expires_at else None,
        "remaining_seconds": remaining,
        "time_limit_minutes": time_limit_minutes,
        "is_expired": remaining == 0 if remaining is not None else False,
    }


def save_answer(attempt_id: int, question_id: int, selected_option_index: int) -> dict:
    """Save (or overwrite) one answer. Returns a small status dict.

    - Tolerant of a late save after the attempt is already submitted /
      timed out (returns saved=False instead of raising) so a client that
      is mid-flush when the server auto-submits doesn't blow up.
    - Race-safe: two concurrent saves for the same (attempt, question) no
      longer 500 on the uq_attempt_question unique constraint.
    """
    attempt = get_attempt(attempt_id)
    if attempt.status != "in_progress":
        return {"saved": False, "reason": "attempt_not_in_progress", "question_id": question_id}
    if _attempt_is_expired(attempt):
        finalize_expired_attempt(attempt.id)
        return {"saved": False, "reason": "time_over", "question_id": question_id}

    db = get_db()
    _upsert_answer(db, attempt_id, question_id, selected_option_index)
    return {"saved": True, "question_id": question_id}


def _upsert_answer(db, attempt_id: int, question_id: int, selected_option_index: int) -> AttemptAnswer:
    from sqlalchemy.exc import IntegrityError

    def _find():
        return (
            db.query(AttemptAnswer)
            .filter(
                AttemptAnswer.attempt_id == attempt_id,
                AttemptAnswer.question_id == question_id,
            )
            .first()
        )

    answer = _find()
    if answer is not None:
        answer.selected_option_index = selected_option_index
        db.commit()
        db.refresh(answer)
        return answer

    answer = AttemptAnswer(
        attempt_id=attempt_id,
        question_id=question_id,
        selected_option_index=selected_option_index,
    )
    db.add(answer)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request inserted the row first - update it instead.
        db.rollback()
        answer = _find()
        if answer is None:
            raise
        answer.selected_option_index = selected_option_index
        db.commit()
    db.refresh(answer)
    return answer


def _run_post_submit_steps(attempt: AssessmentAttempt, assessment) -> None:
    """Mastery refresh, learning-path regen, snapshot, prewarm, assignment
    completion. Every step is best-effort - a failure in one must not lose
    the score that is already committed."""
    db = get_db()
    from app.services.lms import learning_path_service, performance_service

    def step(fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Post-submit step failed for attempt %s: %s", attempt.id, exc)
            db.rollback()

    step(lambda: performance_service.update_topic_scores_from_attempt(attempt.id))
    step(lambda: learning_path_service.refresh_learning_path(attempt.student_id))
    step(lambda: performance_service.create_snapshot(attempt.student_id))

    if assessment.assessment_type == "diagnostic":
        from app.services.lms import deficiency_chat_service, student_profile_service
        from app.tasks.lms_tasks import enqueue_deficiency_chat_prewarm

        step(lambda: deficiency_chat_service._close_old_sessions(attempt.student_id))
        step(
            lambda: student_profile_service.mark_diagnostic_complete(
                attempt.student_id, assessment_id=assessment.id
            )
        )
        step(lambda: enqueue_deficiency_chat_prewarm(attempt.student_id))

    if attempt.assignment_id:
        from app.services.lms import assignment_service

        step(
            lambda: assignment_service.mark_submission_complete(
                attempt.assignment_id, attempt.student_id, attempt.id
            )
        )


def _score_and_finalize(attempt: AssessmentAttempt, assessment, *, timed_out: bool) -> dict:
    """Score whatever the student answered, mark the attempt submitted, run
    post-submit steps, and return the result payload. Shared by
    submit_attempt (normal + client-signalled timeout) and
    finalize_expired_attempt (server-detected timeout).

    Unanswered questions are simply not correct - they never force the
    answered ones to zero.
    """
    db = get_db()
    question_ids = [aq.question_id for aq in assessment.questions]
    questions = db.query(Question).filter(Question.id.in_(question_ids)).all()
    q_by_id = {q.id: q for q in questions}
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt.id).all()
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
    if timed_out:
        attempt.timed_out = True
    db.commit()

    _run_post_submit_steps(attempt, assessment)

    result = {
        "attempt_id": attempt.id,
        "score": correct,
        "max_score": max_score,
        "score_percent": round(100.0 * correct / max_score, 2),
        "topic_breakdown": list(topic_breakdown.values()),
        "assessment_type": assessment.assessment_type,
    }
    if assessment.assessment_type == "diagnostic":
        from app.services.lms import performance_service

        analysis = performance_service.analyze_attempt(attempt.id)
        result["weak_topics"] = analysis.get("weak_topics", [])
        result["strong_topics"] = analysis.get("strong_topics", [])
        result["diagnostic_completed"] = True
    if timed_out:
        result["timed_out"] = True
        result["time_over"] = True
        result["message"] = TIME_OVER_MESSAGE
    return result


def submit_attempt(attempt_id: int, time_expired: bool = False) -> dict:
    attempt = get_attempt(attempt_id)
    if attempt.status == "submitted":
        return get_attempt_results(attempt_id)
    if attempt.status != "in_progress":
        raise LMSValidationError("Attempt is not in progress")

    assessment = get_assessment(attempt.assessment_id)
    timed_out = bool(
        assessment.assessment_type == "diagnostic"
        and (time_expired or _attempt_is_expired(attempt))
    )
    if timed_out:
        logger.info("Diagnostic attempt %s auto-submitted (time over)", attempt_id)
    return _score_and_finalize(attempt, assessment, timed_out=timed_out)


def get_attempt_results(attempt_id: int) -> dict:
    """Return scored attempt summary (idempotent)."""
    attempt = get_attempt(attempt_id)
    if attempt.status != "submitted":
        raise LMSValidationError("Attempt not yet submitted")
    max_score = attempt.max_score or 1.0
    score = attempt.score or 0.0
    assessment = get_assessment(attempt.assessment_id)
    timed_out = bool(getattr(attempt, "timed_out", False))
    result = {
        "attempt_id": attempt.id,
        "score": score,
        "max_score": max_score,
        "score_percent": round(100.0 * score / max_score, 2) if max_score else 0.0,
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        "assessment_type": assessment.assessment_type,
        "timed_out": timed_out,
    }
    if timed_out:
        # The timer ran out, but the answered questions still count - keep
        # the real score, just note that it was auto-submitted.
        result["time_over"] = True
        result["message"] = TIME_OVER_MESSAGE
    if assessment.assessment_type == "diagnostic":
        result["diagnostic_completed"] = True
        from app.services.lms import performance_service

        analysis = performance_service.analyze_attempt(attempt_id)
        result["weak_topics"] = analysis.get("weak_topics", [])
        result["strong_topics"] = analysis.get("strong_topics", [])
    return result


def list_student_attempts(student_id: int, limit: int = 50) -> List[dict]:
    from app.models.lms_models import Assessment

    db = get_db()
    rows = (
        db.query(AssessmentAttempt)
        .filter(AssessmentAttempt.student_id == student_id)
        .order_by(AssessmentAttempt.started_at.desc())
        .limit(limit)
        .all()
    )
    assessment_ids = {a.assessment_id for a in rows}
    assessments = (
        {
            s.id: s
            for s in db.query(Assessment).filter(Assessment.id.in_(assessment_ids)).all()
        }
        if assessment_ids
        else {}
    )
    type_label = {"diagnostic": "Diagnostic Assessment", "quiz": "Quiz"}
    result = []
    for a in rows:
        pct = None
        if a.status == "submitted" and a.max_score:
            pct = round(100.0 * (a.score or 0) / a.max_score, 1)
        s = assessments.get(a.assessment_id)
        a_type = s.assessment_type if s else "quiz"
        if a_type == "diagnostic":
            # Admins title diagnostics "unit22" / "8" / "10" - meaningless to a
            # student; the type is the useful label.
            title = "Diagnostic Assessment"
        else:
            title = (s.title if s and s.title else None) or type_label.get(a_type, "Assessment")
        result.append(
            {
                "attempt_id": a.id,
                "assessment_id": a.assessment_id,
                "assignment_id": a.assignment_id,
                "assessment_type": a_type,
                "title": title,
                "status": a.status,
                "score_percent": pct,
                "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            }
        )
    return result
