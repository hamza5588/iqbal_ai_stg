"""Aggregate per-student / per-question dwell for quiz, Learning Chat, and AI Guided Practice.

Sources (already written by product flows):
- Quiz: ``assessment_attempts.started_at/submitted_at`` + ``attempt_answers.time_spent_seconds``
- Learning Chat: ``deficiency_chat_sessions.questions_json[*].time_spent_seconds``
- Guided Practice (bank): ``lms_guided_practice_attempts.time_spent_seconds``
- AI Guided Practice (PDF): ``lms_aigp_session_items.time_spent_seconds``
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from app.models.lms_models import (
    AIGPSession,
    AIGPSessionItem,
    AssessmentAttempt,
    AttemptAnswer,
    DeficiencyChatSession,
    GuidedPracticeAttempt,
)
REPORT_ZONE = ZoneInfo("Asia/Karachi")


def seconds_to_hms(seconds: Optional[int | float]) -> Optional[str]:
    if seconds is None:
        return None
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def seconds_to_minutes(seconds: Optional[int | float], ndigits: int = 2) -> Optional[float]:
    if seconds is None:
        return None
    return round(max(0, float(seconds)) / 60.0, ndigits)


def _local_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=REPORT_ZONE)
    end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=REPORT_ZONE)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def window_utc(previous_day: Optional[date], quiz_day: date) -> tuple[datetime, datetime]:
    """UTC-naive [start, end) covering activity since the previous quiz day through quiz_day."""
    if previous_day:
        start, _ = _local_day_bounds_utc(previous_day + timedelta(days=1))
    else:
        start, _ = _local_day_bounds_utc(quiz_day - timedelta(days=60))
    _, end = _local_day_bounds_utc(quiz_day)
    if start > end:
        start = end
    return start, end


def quiz_attempt_timing(attempt: AssessmentAttempt, answers: Iterable[AttemptAnswer], ordered_question_ids: list[int]) -> dict:
    """Total wall-clock quiz time plus per-MCQ dwell."""
    wall_seconds = None
    if attempt.started_at and attempt.submitted_at and attempt.submitted_at >= attempt.started_at:
        wall_seconds = int((attempt.submitted_at - attempt.started_at).total_seconds())
    by_qid = {a.question_id: a for a in answers}
    questions = []
    dwell_total = 0
    for number, qid in enumerate(ordered_question_ids, 1):
        row = by_qid.get(qid)
        sec = int(row.time_spent_seconds or 0) if row else 0
        dwell_total += sec
        questions.append(
            {
                "question_number": number,
                "question_id": qid,
                "time_spent_seconds": sec,
                "is_correct": bool(row.is_correct) if row and row.is_correct is not None else None,
            }
        )
    return {
        "attempt_id": attempt.id,
        "student_id": attempt.student_id,
        "assessment_id": attempt.assessment_id,
        "wall_clock_seconds": wall_seconds,
        "wall_clock_minutes": seconds_to_minutes(wall_seconds),
        "question_dwell_total_seconds": dwell_total,
        "questions": questions,
    }


def _parse_chat_questions(raw) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def learning_chat_session_timing(session: DeficiencyChatSession) -> dict:
    """Total + per-question Learning Chat dwell from questions_json."""
    questions = []
    total = 0
    for idx, q in enumerate(_parse_chat_questions(session.questions_json), 1):
        if not isinstance(q, dict):
            continue
        try:
            sec = max(0, int(q.get("time_spent_seconds") or 0))
        except (TypeError, ValueError):
            sec = 0
        total += sec
        questions.append(
            {
                "question_number": idx,
                "question_ref": q.get("id") or q.get("question_id") or q.get("bank_item_id"),
                "time_spent_seconds": sec,
                "is_correct": q.get("is_correct"),
                "status": q.get("status"),
            }
        )
    return {
        "session_id": session.id,
        "student_id": session.student_id,
        "status": session.status,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "total_time_spent_seconds": total,
        "total_time_hms": seconds_to_hms(total),
        "questions": questions,
    }


def guided_practice_item_timing(attempt: GuidedPracticeAttempt) -> dict:
    return {
        "source": "guided_practice_bank",
        "attempt_id": attempt.id,
        "student_id": attempt.student_id,
        "bank_item_id": attempt.bank_item_id,
        "time_spent_seconds": int(attempt.time_spent_seconds or 0),
        "answered_at": attempt.answered_at.isoformat() if attempt.answered_at else None,
        "is_correct": attempt.is_correct,
    }


def aigp_item_timing(session: AIGPSession, item: AIGPSessionItem) -> dict:
    return {
        "source": "ai_guided_practice",
        "session_id": session.id,
        "student_id": session.student_id,
        "item_id": item.id,
        "question_id": item.question_id,
        "position": item.position,
        "time_spent_seconds": int(item.time_spent_seconds or 0),
        "answered_at": item.answered_at.isoformat() if item.answered_at else None,
        "is_correct": item.is_correct,
    }


def load_learning_chat_timings(db, student_ids: Iterable[int], start_utc: datetime, end_utc: datetime) -> dict[int, dict]:
    """student_id -> aggregated Learning Chat timing in window."""
    ids = list(student_ids) or [-1]
    rows = (
        db.query(DeficiencyChatSession)
        .filter(
            DeficiencyChatSession.student_id.in_(ids),
            DeficiencyChatSession.updated_at >= start_utc,
            DeficiencyChatSession.updated_at < end_utc,
        )
        .all()
    )
    out: dict[int, dict] = {}
    for session in rows:
        timing = learning_chat_session_timing(session)
        bucket = out.setdefault(
            session.student_id,
            {
                "total_time_spent_seconds": 0,
                "sessions": [],
                "questions": [],
            },
        )
        bucket["total_time_spent_seconds"] += int(timing["total_time_spent_seconds"] or 0)
        bucket["sessions"].append(timing)
        bucket["questions"].extend(
            [{**q, "session_id": session.id} for q in timing["questions"]]
        )
    for student_id, bucket in out.items():
        bucket["total_time_hms"] = seconds_to_hms(bucket["total_time_spent_seconds"])
    return out


def load_guided_practice_timings(db, student_ids: Iterable[int], start_utc: datetime, end_utc: datetime) -> dict[int, dict]:
    """student_id -> bank GP + AIGP dwell in window (combined AI-Guided Practice)."""
    ids = list(student_ids) or [-1]
    out: dict[int, dict] = {}

    def bucket_for(student_id: int) -> dict:
        return out.setdefault(
            student_id,
            {
                "total_time_spent_seconds": 0,
                "bank_questions": [],
                "aigp_questions": [],
            },
        )

    bank_rows = (
        db.query(GuidedPracticeAttempt)
        .filter(
            GuidedPracticeAttempt.student_id.in_(ids),
            GuidedPracticeAttempt.answered_at.isnot(None),
            GuidedPracticeAttempt.answered_at >= start_utc,
            GuidedPracticeAttempt.answered_at < end_utc,
        )
        .all()
    )
    for attempt in bank_rows:
        item = guided_practice_item_timing(attempt)
        bucket = bucket_for(attempt.student_id)
        bucket["total_time_spent_seconds"] += int(item["time_spent_seconds"] or 0)
        bucket["bank_questions"].append(item)

    aigp_rows = (
        db.query(AIGPSession, AIGPSessionItem)
        .join(AIGPSessionItem, AIGPSessionItem.session_id == AIGPSession.id)
        .filter(
            AIGPSession.student_id.in_(ids),
            AIGPSessionItem.answered_at.isnot(None),
            AIGPSessionItem.answered_at >= start_utc,
            AIGPSessionItem.answered_at < end_utc,
        )
        .all()
    )
    for session, item in aigp_rows:
        timing = aigp_item_timing(session, item)
        bucket = bucket_for(session.student_id)
        bucket["total_time_spent_seconds"] += int(timing["time_spent_seconds"] or 0)
        bucket["aigp_questions"].append(timing)

    for student_id, bucket in out.items():
        bucket["total_time_hms"] = seconds_to_hms(bucket["total_time_spent_seconds"])
        bucket["questions"] = bucket["bank_questions"] + bucket["aigp_questions"]
    return out


def activity_times_for_window(
    db,
    student_ids: Iterable[int],
    previous_day: Optional[date],
    quiz_day: date,
) -> dict[int, dict]:
    """Per-student Learning Chat + AI-Guided Practice totals between quizzes."""
    start_utc, end_utc = window_utc(previous_day, quiz_day)
    chat = load_learning_chat_timings(db, student_ids, start_utc, end_utc)
    practice = load_guided_practice_timings(db, student_ids, start_utc, end_utc)
    ids = set(chat) | set(practice) | set(student_ids)
    result = {}
    for student_id in ids:
        c = chat.get(student_id) or {"total_time_spent_seconds": 0, "sessions": [], "questions": []}
        p = practice.get(student_id) or {
            "total_time_spent_seconds": 0,
            "bank_questions": [],
            "aigp_questions": [],
            "questions": [],
        }
        result[student_id] = {
            "window_start_utc": start_utc.isoformat(),
            "window_end_utc": end_utc.isoformat(),
            "learning_chat": c,
            "ai_guided_practice": p,
            "learning_chat_seconds": int(c.get("total_time_spent_seconds") or 0),
            "ai_guided_practice_seconds": int(p.get("total_time_spent_seconds") or 0),
            "learning_chat_hms": seconds_to_hms(int(c.get("total_time_spent_seconds") or 0)),
            "ai_guided_practice_hms": seconds_to_hms(int(p.get("total_time_spent_seconds") or 0)),
        }
    return result


def quiz_timings_for_attempts(db, attempts: list[AssessmentAttempt], ordered_ids: dict[int, list[int]]) -> dict[int, dict]:
    """attempt_id -> quiz timing breakdown."""
    attempt_ids = [a.id for a in attempts] or [-1]
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id.in_(attempt_ids)).all()
    by_attempt: dict[int, list[AttemptAnswer]] = {}
    for row in answers:
        by_attempt.setdefault(row.attempt_id, []).append(row)
    return {
        attempt.id: quiz_attempt_timing(attempt, by_attempt.get(attempt.id, []), ordered_ids.get(attempt.id, []))
        for attempt in attempts
    }
