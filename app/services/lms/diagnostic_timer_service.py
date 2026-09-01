"""AI-based per-question time estimation for diagnostic assessments."""
from __future__ import annotations

import json
import logging
from typing import List

from pydantic import BaseModel, Field

from app.models.lms_models import Assessment, Question
from app.services.lms.assessment_service import get_assessment
from app.services.lms.exceptions import LMSNotFoundError
from app.services.lms.mcq_utils import options_from_json
from app.utils.db import get_db
from app.utils.llm_factory import get_chat_model

logger = logging.getLogger(__name__)

_DIFFICULTY_SECONDS = {"easy": 45, "medium": 90, "hard": 180}
_MIN_SECONDS = 15
_MAX_SECONDS = 300
_BUFFER_RATIO = 0.10


class QuestionTimeEstimate(BaseModel):
    question_index: int = Field(..., ge=0)
    difficulty: str = Field(..., pattern=r"^(easy|medium|hard)$")
    time_limit_seconds: int = Field(..., ge=_MIN_SECONDS, le=_MAX_SECONDS)


class DiagnosticTimeBatch(BaseModel):
    estimates: List[QuestionTimeEstimate]


_TIMER_PROMPT = """Analyze each multiple-choice diagnostic question and estimate how long a typical student
needs to read, think, and answer it.

For each question return:
- difficulty: easy | medium | hard
- time_limit_seconds: integer between 15 and 300 (e.g. simple recall ~30s, multi-step problem ~180s)

Questions (JSON array):
{questions_json}
"""


def _heuristic_seconds(question_text: str, options: list, difficulty: str) -> int:
    """Fallback when LLM is unavailable."""
    base = _DIFFICULTY_SECONDS.get(difficulty, 90)
    text_len = len(question_text or "")
    if text_len > 400:
        base += 30
    elif text_len > 200:
        base += 15
    if any(len((o.get("text") or "")) > 120 for o in options):
        base += 15
    return max(_MIN_SECONDS, min(_MAX_SECONDS, base))


def _questions_payload(assessment: Assessment) -> List[dict]:
    db = get_db()
    payload = []
    for idx, aq in enumerate(sorted(assessment.questions, key=lambda x: x.sort_order)):
        q = db.query(Question).filter(Question.id == aq.question_id).first()
        if not q:
            continue
        opts = options_from_json(q.options_json)
        payload.append(
            {
                "index": idx,
                "question_text": (q.question_text or "")[:500],
                "options": [o.get("text", "")[:120] for o in opts],
            }
        )
    return payload


def _apply_estimates(assessment_id: int, estimates: List[QuestionTimeEstimate]) -> int:
    assessment = get_assessment(assessment_id)
    db = get_db()
    sorted_aq = sorted(assessment.questions, key=lambda x: x.sort_order)
    total = 0
    for est in estimates:
        if est.question_index >= len(sorted_aq):
            continue
        qid = sorted_aq[est.question_index].question_id
        q = db.query(Question).filter(Question.id == qid).first()
        if not q:
            continue
        q.difficulty = est.difficulty
        q.time_limit_seconds = est.time_limit_seconds
        total += est.time_limit_seconds
    assessment.time_limit_minutes = max(1, int((total * (1 + _BUFFER_RATIO) + 59) // 60))
    db.commit()
    return total


def estimate_question_times(assessment_id: int) -> dict:
    """Use LLM to assign difficulty and per-question time limits; fallback to heuristics."""
    assessment = get_assessment(assessment_id)
    if assessment.assessment_type != "diagnostic":
        raise LMSNotFoundError("Not a diagnostic assessment")

    payload = _questions_payload(assessment)
    if not payload:
        return {"question_count": 0, "total_seconds": 0, "method": "none"}

    estimates: List[QuestionTimeEstimate] = []
    method = "heuristic"

    try:
        llm = get_chat_model(temperature=0.1)
        structured = llm.with_structured_output(DiagnosticTimeBatch)
        prompt = _TIMER_PROMPT.format(questions_json=json.dumps(payload, ensure_ascii=False))
        result: DiagnosticTimeBatch = structured.invoke(prompt)
        if result.estimates:
            estimates = result.estimates
            method = "ai"
    except Exception as exc:
        logger.warning("AI timer estimation failed for assessment %s: %s", assessment_id, exc)

    if not estimates:
        db = get_db()
        sorted_aq = sorted(assessment.questions, key=lambda x: x.sort_order)
        for idx, _item in enumerate(payload):
            if idx >= len(sorted_aq):
                break
            q = db.query(Question).filter(Question.id == sorted_aq[idx].question_id).first()
            if not q:
                continue
            diff = q.difficulty if q.difficulty in _DIFFICULTY_SECONDS else "medium"
            opts = options_from_json(q.options_json)
            secs = _heuristic_seconds(q.question_text, opts, diff)
            estimates.append(
                QuestionTimeEstimate(question_index=idx, difficulty=diff, time_limit_seconds=secs)
            )
        method = "heuristic"

    total = _apply_estimates(assessment_id, estimates)
    return {
        "question_count": len(estimates),
        "total_seconds": int(total * (1 + _BUFFER_RATIO)),
        "time_limit_minutes": assessment.time_limit_minutes,
        "method": method,
    }


def compute_attempt_deadline(assessment_id: int) -> int:
    """Return total allowed seconds for a diagnostic attempt (sum + buffer)."""
    assessment = get_assessment(assessment_id)
    db = get_db()
    total = 0
    for aq in sorted(assessment.questions, key=lambda x: x.sort_order):
        q = db.query(Question).filter(Question.id == aq.question_id).first()
        if not q:
            continue
        if q.time_limit_seconds:
            total += q.time_limit_seconds
        else:
            total += _DIFFICULTY_SECONDS.get(q.difficulty, 90)
    if total <= 0:
        total = (assessment.time_limit_minutes or 30) * 60
    return int(total * (1 + _BUFFER_RATIO))


def get_question_time_limits(assessment_id: int) -> List[dict]:
    """Per-question time limits for delivery UI."""
    assessment = get_assessment(assessment_id)
    db = get_db()
    result = []
    for aq in sorted(assessment.questions, key=lambda x: x.sort_order):
        q = db.query(Question).filter(Question.id == aq.question_id).first()
        if not q:
            continue
        secs = q.time_limit_seconds or _DIFFICULTY_SECONDS.get(q.difficulty, 90)
        result.append(
            {
                "question_id": q.id,
                "difficulty": q.difficulty,
                "time_limit_seconds": secs,
            }
        )
    return result
