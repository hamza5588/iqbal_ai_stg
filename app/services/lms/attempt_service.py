"""Assessment attempt and scoring service."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.models.lms_models import AssessmentAttempt, AttemptAnswer, Question, StudentProfile
from app.services.lms.assessment_service import get_assessment
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError, UnansweredQuestionsError
from app.services.lms.mcq_utils import options_from_json
from app.utils.db import get_db

logger = logging.getLogger(__name__)


def attempt_question_ids(attempt: AssessmentAttempt) -> List[int]:
    """Ordered question ids for this attempt: its own list when set (a
    diagnostic retake carries a shuffled / AI-variant set), otherwise the
    assessment's own questions."""
    raw = getattr(attempt, "question_ids_json", None)
    if raw:
        try:
            ids = json.loads(raw)
            if isinstance(ids, list) and ids:
                return [int(x) for x in ids]
        except (ValueError, TypeError):
            logger.warning("Bad question_ids_json on attempt %s", attempt.id)
    assessment = get_assessment(attempt.assessment_id)
    return [aq.question_id for aq in sorted(assessment.questions, key=lambda x: x.sort_order)]

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


# Offline time credit (diagnostics only). The quiz page pings /heartbeat every
# HEARTBEAT_INTERVAL_SECONDS; a silence longer than OFFLINE_GAP_SECONDS means the student
# lost their connection (or the device slept), and that time is added back to the deadline.
# A closed tab looks the same as a dropped connection, so the total is capped: nobody can
# pause the clock for more than MAX_OFFLINE_CREDIT_SECONDS per attempt.
HEARTBEAT_INTERVAL_SECONDS = 15
OFFLINE_GAP_SECONDS = 90  # background tabs are throttled to ~1 ping/minute; that is not "offline"
MAX_OFFLINE_CREDIT_SECONDS = 10 * 60


def _is_owner_request(attempt: AssessmentAttempt) -> bool:
    """True only while serving a request made by the student who owns the attempt."""
    try:
        from flask import has_request_context, session

        return has_request_context() and session.get("user_id") == attempt.student_id
    except Exception:  # noqa: BLE001
        return False


def _touch_attempt(attempt: AssessmentAttempt) -> int:
    """Record that the owner is connected now; returns seconds credited for the gap before it."""
    if attempt.status != "in_progress":
        return 0
    now = datetime.utcnow()
    credit = 0
    last_seen = getattr(attempt, "last_seen_at", None)
    if attempt.expires_at and last_seen:
        gap = (now - last_seen).total_seconds()
        if gap >= OFFLINE_GAP_SECONDS and get_assessment(attempt.assessment_id).assessment_type == "diagnostic":
            room = MAX_OFFLINE_CREDIT_SECONDS - int(attempt.offline_credit_seconds or 0)
            credit = max(0, int(min(gap - HEARTBEAT_INTERVAL_SECONDS, room)))
            if credit:
                attempt.expires_at = attempt.expires_at + timedelta(seconds=credit)
                attempt.offline_credit_seconds = int(attempt.offline_credit_seconds or 0) + credit
                logger.info("Attempt %s: credited %ss of offline time (gap %.0fs)", attempt.id, credit, gap)
    attempt.last_seen_at = now
    get_db().commit()
    return credit


def heartbeat(attempt_id: int) -> dict:
    """Ping from the open quiz page: keeps last_seen fresh and returns the authoritative timer."""
    attempt = get_attempt(attempt_id)
    credited = 0
    if attempt.status == "in_progress":
        credited = _touch_attempt(attempt)
        if _attempt_is_expired(attempt):
            finalize_expired_attempt(attempt.id)
            attempt = get_attempt(attempt_id)
    info = get_attempt_timer_info(attempt_id)
    info.update({
        "status": attempt.status,
        "credited_seconds": credited,
        "offline_credit_seconds": int(getattr(attempt, "offline_credit_seconds", 0) or 0),
    })
    return info


def _attempt_is_expired(attempt: AssessmentAttempt) -> bool:
    if attempt.status == "in_progress" and attempt.expires_at and _is_owner_request(attempt):
        # give back offline time before deciding the clock has run out
        if datetime.utcnow() >= attempt.expires_at or (
            attempt.last_seen_at and (datetime.utcnow() - attempt.last_seen_at).total_seconds() >= OFFLINE_GAP_SECONDS
        ):
            _touch_attempt(attempt)
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


def _submitted_diagnostic_count(student_id: int, assessment_id: int) -> int:
    db = get_db()
    return (
        db.query(AssessmentAttempt)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.assessment_id == assessment_id,
            AssessmentAttempt.status == "submitted",
        )
        .count()
    )


def start_attempt(
    student_id: int,
    assessment_id: int,
    assignment_id: Optional[int] = None,
    retake: bool = False,
) -> tuple[AssessmentAttempt, bool, bool]:
    """Start or resume an attempt. Returns (attempt, resumed, already_completed).

    ``already_completed`` is True when this call did not start/resume a live
    attempt but instead handed back a previously-finished one because
    retakes aren't allowed (diagnostic: ever; quiz: raises instead, see
    below) -- so callers can be explicit about it instead of having to
    guess from response shape alone (quiz raises LMSValidationError with a
    clear message on retake; diagnostic returns 200 with the existing
    attempt, so it needs its own explicit flag).
    """
    assessment = get_assessment(assessment_id)
    # A diagnostic the admin removed / replaced stays startable for students of that
    # grade who still owe it (checked against the student's queue below).
    replaced_diagnostic = (
        assessment.assessment_type == "diagnostic"
        and assessment.status == "archived"
        and assessment.superseded_at is not None
    )
    if assessment.status != "published" and not replaced_diagnostic:
        raise LMSValidationError("Assessment is not published")

    is_diagnostic_retake = False
    if assessment.assessment_type == "diagnostic":
        from app.services.lms import diagnostic_service

        # Must match GET /diagnostics/default: the diagnostics for THIS student's
        # grade — the published one, plus replaced ones they have not taken yet.
        queue_ids = [a.id for a in diagnostic_service.get_student_diagnostic_queue(student_id)]
        if assessment_id not in queue_ids:
            raise LMSValidationError("This diagnostic is not available")
        assignment_id = None
        timeout = finalize_expired_diagnostic_if_needed(student_id, assessment_id)
        if timeout and not retake:
            return get_attempt(timeout["attempt_id"]), False, True
        # "Completed" is per diagnostic: a student who finished an earlier diagnostic
        # takes a newly published one as a normal first attempt. Only a second go at a
        # diagnostic they already submitted is a retake (teacher approval, checked below).
        latest = get_latest_submitted_attempt(student_id, assessment_id)
        if latest:
            if not retake or replaced_diagnostic:
                return latest, False, True
            is_diagnostic_retake = True
        elif not _find_in_progress_attempt(student_id, assessment_id):
            # In order: an earlier diagnostic the student still owes comes first.
            pending = diagnostic_service.get_student_pending_diagnostics(student_id)
            if pending and pending[0].id != assessment_id:
                raise LMSValidationError(
                    "Please complete your earlier diagnostic assessment first, then start this one."
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
        return existing, True, False

    if assessment.assessment_type == "quiz" and _student_completed_quiz(student_id, assessment_id):
        raise LMSValidationError(
            "You have already completed this quiz. Retakes are not allowed. "
            "If your teacher needs another attempt, they must create and assign a different quiz."
        )

    if is_diagnostic_retake:
        # Not self-service: needs an approved request (raises with the reason otherwise).
        from app.services.lms import retake_request_service

        retake_request_service.consume_approval(student_id, assessment_id)

    attempt = AssessmentAttempt(
        student_id=student_id,
        assessment_id=assessment_id,
        assignment_id=assignment_id,
        status="in_progress",
        max_score=float(len(assessment.questions)),
        last_seen_at=datetime.utcnow(),
    )

    if is_diagnostic_retake:
        from app.services.lms import diagnostic_variant_service

        attempt_number = _submitted_diagnostic_count(student_id, assessment_id) + 1
        try:
            q_ids = diagnostic_variant_service.question_set_for_attempt(
                assessment_id, attempt_number, student_id=student_id
            )
            if q_ids:
                import json as _json

                attempt.question_ids_json = _json.dumps(q_ids)
                attempt.max_score = float(len(q_ids))
        except Exception as exc:  # noqa: BLE001 - never block a retake
            logger.warning("Retake question-set build failed for student %s: %s", student_id, exc)

    if assessment.assessment_type == "diagnostic":
        from app.services.lms.diagnostic_timer_service import compute_attempt_deadline

        total_seconds = compute_attempt_deadline(assessment_id)
        attempt.expires_at = datetime.utcnow() + timedelta(seconds=total_seconds)
    elif (
        assessment.assessment_type == "quiz"
        and assessment.time_limit_minutes
        and int(assessment.time_limit_minutes) > 0
    ):
        attempt.expires_at = datetime.utcnow() + timedelta(
            minutes=int(assessment.time_limit_minutes)
        )

    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    if is_diagnostic_retake:
        try:
            from app.services.lms import diagnostic_variant_service

            diagnostic_variant_service.enqueue_pool_prewarm(assessment_id)
        except Exception:  # noqa: BLE001
            pass

    return attempt, False, False


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
        if a.selected_option_index is not None and a.selected_option_index >= 0
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
    from app.services.quiz.display_format_qa import (
        finalize_display_text,
        finalize_option_text,
        student_render,
    )

    db = get_db()
    q_ids = attempt_question_ids(attempt)
    q_rows = {r.id: r for r in db.query(Question).filter(Question.id.in_(q_ids)).all()}
    result = []
    for sort_order, qid in enumerate(q_ids):
        q = q_rows.get(qid)
        if not q:
            continue
        opts = options_from_json(q.options_json)
        q_text, q_latex = finalize_display_text(q.question_text, q.question_latex)
        safe_opts = []
        for o in opts:
            text, latex = finalize_option_text(o.get("text"), o.get("latex"))
            safe_opts.append(
                {
                    "label": o.get("label"),
                    "text": text,
                    "latex": latex,
                    "render": student_render(text, latex, inline=True),
                }
            )
        result.append(
            {
                "question_id": q.id,
                "question_text": q_text or q.question_text,
                "question_latex": q_latex,
                "question_render": student_render(q_text, q_latex, inline=False),
                "options": safe_opts,
                "sort_order": sort_order,
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
    if question_id not in attempt_question_ids(attempt):
        raise LMSValidationError("That question is not part of this attempt")
    if selected_option_index is not None and selected_option_index >= 0:
        question = db.query(Question).filter(Question.id == question_id).first()
        option_count = len(options_from_json(question.options_json)) if question else 0
        if selected_option_index >= option_count:
            raise LMSValidationError("That option does not exist for this question")
    if selected_option_index is None or selected_option_index < 0:
        # A cleared answer - remove the row so it reads as unanswered.
        db.query(AttemptAnswer).filter(
            AttemptAnswer.attempt_id == attempt_id,
            AttemptAnswer.question_id == question_id,
        ).delete(synchronize_session=False)
        db.commit()
        return {"saved": True, "cleared": True, "question_id": question_id}

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

    # Capture every field a step (or its failure-logging) needs into plain
    # values BEFORE any step runs. Found via E2E test: a step that fails a
    # flush (e.g. the enrichment-item CHECK-constraint bug) leaves the
    # session's pending transaction rolled back; touching attempt.<attr>
    # afterwards - even just to log which attempt failed - forces SQLAlchemy
    # to reload it from that same broken session, which raises
    # PendingRollbackError *inside* the except block and escapes step(),
    # turning one best-effort step's failure into a 500 on the whole submit.
    attempt_id = attempt.id
    student_id = attempt.student_id
    assignment_id = attempt.assignment_id
    assessment_id = assessment.id
    assessment_type = assessment.assessment_type

    def step(fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Post-submit step failed for attempt %s: %s", attempt_id, exc)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass

    step(lambda: performance_service.update_topic_scores_from_attempt(attempt_id))
    step(lambda: learning_path_service.refresh_learning_path(student_id))
    step(lambda: performance_service.create_snapshot(student_id))

    if assessment_type == "diagnostic":
        from app.services.lms import deficiency_chat_service, student_profile_service
        from app.tasks.lms_tasks import enqueue_deficiency_chat_prewarm

        step(lambda: deficiency_chat_service._close_old_sessions(student_id))
        step(
            lambda: student_profile_service.mark_diagnostic_complete(
                student_id, assessment_id=assessment_id
            )
        )
        step(lambda: enqueue_deficiency_chat_prewarm(student_id))

    if assignment_id:
        from app.services.lms import assignment_service

        step(
            lambda: assignment_service.mark_submission_complete(
                assignment_id, student_id, attempt_id
            )
        )


def _score_and_finalize(attempt: AssessmentAttempt, assessment, *, timed_out: bool) -> dict:
    """Score whatever the student answered, mark the attempt submitted, run
    post-submit steps, and return the result payload. Shared by
    submit_attempt (normal + client-signalled timeout) and
    finalize_expired_attempt (server-detected timeout).

    Unanswered / skipped questions count as incorrect (0 points) toward
    overall score; they never force already-answered questions to zero.
    """
    db = get_db()
    question_ids = attempt_question_ids(attempt)
    questions = db.query(Question).filter(Question.id.in_(question_ids)).all()
    q_by_id = {q.id: q for q in questions}
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt.id).all()
    answer_by_q = {a.question_id: a for a in answers}

    correct = 0
    answered_count = 0
    topic_breakdown: Dict[int, dict] = {}
    for qid in question_ids:
        q = q_by_id.get(qid)
        if not q:
            continue
        ans = answer_by_q.get(qid)
        has_answer = (
            ans is not None
            and ans.selected_option_index is not None
            and ans.selected_option_index >= 0
        )
        if has_answer:
            answered_count += 1
        is_correct = (
            has_answer and ans.selected_option_index == q.correct_option_index
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
    unanswered_count = max(0, len(question_ids) - answered_count)
    attempt.score = correct
    attempt.max_score = max_score
    attempt.status = "submitted"
    attempt.submitted_at = datetime.utcnow()
    if timed_out:
        attempt.timed_out = True
    db.commit()

    _run_post_submit_steps(attempt, assessment)

    from app.services.lms import performance_service

    enriched_breakdown = performance_service.topic_breakdown_for_attempt(attempt.id)
    result = {
        "attempt_id": attempt.id,
        "score": correct,
        "max_score": max_score,
        "score_percent": round(100.0 * correct / max_score, 2),
        "answered_count": answered_count,
        "unanswered_count": unanswered_count,
        "topic_breakdown": enriched_breakdown or list(topic_breakdown.values()),
        "assessment_type": assessment.assessment_type,
    }
    analysis = performance_service.analyze_attempt(attempt.id)
    result["weak_topics"] = analysis.get("weak_topics", [])
    result["strong_topics"] = analysis.get("strong_topics", [])
    result["all_topics"] = analysis.get("all_topics") or [
        {
            "topic_id": t["topic_id"],
            "topic_name": t.get("topic_name"),
            "score_percent": t.get("score_percent"),
            "correct": t.get("correct"),
            "total": t.get("total"),
        }
        for t in enriched_breakdown
    ]
    if assessment.assessment_type == "diagnostic":
        result["diagnostic_completed"] = True
    if timed_out:
        result["timed_out"] = True
        result["time_over"] = True
        result["message"] = TIME_OVER_MESSAGE
    return result


def unanswered_question_numbers(attempt: AssessmentAttempt) -> List[int]:
    """1-based positions (as the student sees them) of the questions with no answer yet."""
    db = get_db()
    answered = {
        a.question_id
        for a in db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt.id).all()
        if a.selected_option_index is not None and a.selected_option_index >= 0
    }
    return [pos for pos, qid in enumerate(attempt_question_ids(attempt), start=1) if qid not in answered]


def submit_attempt(attempt_id: int, time_expired: bool = False, require_all_answered: bool = False) -> dict:
    """Score and close an attempt.

    ``require_all_answered`` (the student submit route) refuses a submit while any
    question is unanswered. Running out of time is the one exception: a timed
    attempt whose clock has ended is scored on whatever was answered.
    """
    attempt = get_attempt(attempt_id)
    if attempt.status == "submitted":
        return get_attempt_results(attempt_id)
    if attempt.status != "in_progress":
        raise LMSValidationError("Attempt is not in progress")

    if require_all_answered:
        # The browser's "time is up" only counts on an attempt that really has a deadline.
        out_of_time = _attempt_is_expired(attempt) or bool(time_expired and attempt.expires_at)
        if not out_of_time:
            missing = unanswered_question_numbers(attempt)
            if missing:
                raise UnansweredQuestionsError(missing)

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
    question_ids = attempt_question_ids(attempt)
    db = get_db()
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt.id).all()
    answered_ids = {
        a.question_id
        for a in answers
        if a.selected_option_index is not None and a.selected_option_index >= 0
    }
    answered_count = sum(1 for qid in question_ids if qid in answered_ids)
    unanswered_count = max(0, len(question_ids) - answered_count)
    from app.services.lms import performance_service

    # Read-only: topic mastery is written once, when the attempt is submitted
    # (_score_and_finalize). Re-writing it here on every view overwrote Learning
    # Chat progress and made Weak Topics / Overall Progress / teacher analytics
    # change whenever anyone opened a result.
    analysis = performance_service.analyze_attempt(attempt_id)
    if assessment.assessment_type == "diagnostic":
        from app.models.lms_models import StudentTopicScore

        # One-time self-heal only: the post-submit mastery write is best-effort,
        # so if it failed the student would have no topic scores at all.
        has_scores = (
            db.query(StudentTopicScore.id)
            .filter(StudentTopicScore.student_id == attempt.student_id)
            .first()
        )
        if not has_scores:
            try:
                performance_service.update_topic_scores_from_attempt(attempt_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mastery backfill from diagnostic results failed: %s", exc)
    breakdown = performance_service.topic_breakdown_for_attempt(attempt_id)
    result = {
        "attempt_id": attempt.id,
        "score": score,
        "max_score": max_score,
        "score_percent": round(100.0 * score / max_score, 2) if max_score else 0.0,
        "answered_count": answered_count,
        "unanswered_count": unanswered_count,
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        "assessment_type": assessment.assessment_type,
        "timed_out": timed_out,
        "topic_breakdown": breakdown,
    }
    if timed_out:
        # The timer ran out, but the answered questions still count - keep
        # the real score, just note that it was auto-submitted.
        result["time_over"] = True
        result["message"] = TIME_OVER_MESSAGE
    result["weak_topics"] = analysis.get("weak_topics", [])
    result["strong_topics"] = analysis.get("strong_topics", [])
    result["all_topics"] = analysis.get("all_topics") or [
        {
            "topic_id": t["topic_id"],
            "topic_name": t.get("topic_name"),
            "score_percent": t.get("score_percent"),
            "correct": t.get("correct"),
            "total": t.get("total"),
        }
        for t in breakdown
    ]
    if assessment.assessment_type == "diagnostic":
        result["diagnostic_completed"] = True
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
        score_val = None
        max_score_val = None
        if a.status == "submitted":
            score_val = a.score
            max_score_val = a.max_score
        result.append(
            {
                "attempt_id": a.id,
                "assessment_id": a.assessment_id,
                "assignment_id": a.assignment_id,
                "assessment_type": a_type,
                "title": title,
                "status": a.status,
                "score": score_val,
                "max_score": max_score_val,
                "score_percent": pct,
                "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            }
        )
    return result
