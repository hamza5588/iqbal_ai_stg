"""Guided practice sessions (Phase 5)."""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.models.lms_models import PracticeAttempt, PracticeSession, Question
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.mcq_utils import options_from_json
from app.utils.db import get_db

logger = logging.getLogger(__name__)


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


_HINT_PROMPT = """A student is stuck on this multiple-choice practice question and asked for a hint.
Write ONE hint for them.

{level_rule}

STRICT RULES:
- Do NOT give the final answer, and do NOT say or imply which option is right or wrong.
- Do NOT mention the options at all: never quote an option's value and never go through
  the options one by one.
- Do NOT repeat or re-word the question - the student can already see it.
- Speak to the student directly, in simple English. {length_rule}
- Plain text only. Write maths inline like x^2 or 3/4.

QUESTION:
{stem}

OPTIONS (for your context only - never refer to them):
{options}

Hint:"""

_HINT_LEVEL_RULES = {
    1: (
        "Hint level 1 (gentle nudge): name the idea, rule or definition this question is about "
        "and what to look at first. Do not start solving it.",
        "1 to 2 short sentences.",
    ),
    2: (
        "Hint level 2 (stronger): explain the method and show how to set up the FIRST step for "
        "this question, then stop - leave the rest of the working and the result to the student.",
        "2 to 3 short sentences.",
    ),
}

# What the student sees when no hint could be generated. Never the question again.
_HINT_FALLBACKS = {
    1: "Work out which topic or rule this question is testing, then recall the definition or "
       "formula for it before you look at the options.",
    2: "Write down what the question gives you and what it asks for, apply the rule one step at "
       "a time on paper, and only then compare your result with the options.",
}

_HINT_LEAK_RE = re.compile(
    r"\b(the (correct |right )?answer is|correct (option|answer|choice)|right (option|answer|choice)"
    r"|option [a-d]\b|choice [a-d]\b|is the answer)\b",
    re.I,
)
# (question_id, level) -> hint. The hint depends only on the question, so every
# student asking about the same question reuses it instead of a new LLM call.
_HINT_CACHE: dict = {}


def _hint_gives_answer(hint: str, question: Question, options: list) -> bool:
    if _HINT_LEAK_RE.search(hint):
        return True
    idx = question.correct_option_index
    if not isinstance(idx, int) or not 0 <= idx < len(options):
        return False
    answer = re.sub(r"\s+", " ", str(options[idx].get("text") or "")).strip().lower()
    stem = (question.question_text or "").lower()
    # A value that already appears in the question (or a single character) proves nothing.
    if len(answer) < 2 or answer in stem:
        return False
    # whole value only: "3" must not match inside "30" or "3.5", but "30." at a sentence end does
    return re.search(r"(?<!\w)(?<!\d\.)" + re.escape(answer) + r"(?!\w|\.\d)", hint.lower()) is not None


def _generate_hint(question: Question, level: int) -> Optional[str]:
    """A real hint for this question from the LLM, or None (caller falls back)."""
    key = (question.id, level)
    if key in _HINT_CACHE:
        return _HINT_CACHE[key]
    try:
        from app.services.lms.mcq_utils import pick_display_fields
        from app.utils.groq_rate_limit import invoke_with_groq_rate_limit
        from app.utils.llm_factory import get_chat_model

        stem, _ = pick_display_fields(question.question_text, question.question_latex)
        stem = (stem or question.question_text or "").strip()
        if not stem:
            return None
        options = options_from_json(question.options_json)
        level_rule, length_rule = _HINT_LEVEL_RULES[level]
        prompt = _HINT_PROMPT.format(
            level_rule=level_rule,
            length_rule=length_rule,
            stem=stem[:800],
            options="\n".join("- " + str(o.get("text") or "")[:120] for o in options),
        )
        base_llm = get_chat_model(temperature=0.3, max_tokens=1024)
        # Reasoning knobs differ per model (see question_clarify_service): Qwen takes
        # "none", GPT-OSS only low/medium/high; the model's own defaults always work.
        model_name = str(getattr(base_llm, "model_name", "") or getattr(base_llm, "model", "")).lower()
        first = (
            {"reasoning_effort": "none", "reasoning_format": "hidden"}
            if "qwen" in model_name
            else {"reasoning_effort": "low"}
        )
        for bind_kwargs in (first, {}):
            try:
                llm = base_llm.bind(**bind_kwargs) if bind_kwargs else base_llm
                resp = invoke_with_groq_rate_limit(
                    lambda: llm.invoke(prompt), description="guided practice hint"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Practice hint LLM call failed for q%s: %s", question.id, exc)
                continue
            content = getattr(resp, "content", "")
            text = re.sub(r"\s+", " ", content if isinstance(content, str) else "").strip()
            text = re.sub(r"^hint\s*:\s*", "", text, flags=re.I)[:500]
            if not text:
                continue
            if _hint_gives_answer(text, question, options):
                logger.info("Practice hint for q%s rejected: it gives the answer away", question.id)
                return None
            _HINT_CACHE[key] = text
            return text
    except Exception as exc:  # noqa: BLE001 - a hint must never break practice
        logger.warning("Practice hint generation failed for q%s: %s", question.id, exc)
    return None


def request_hint(session_id: int, student_id: Optional[int] = None) -> dict:
    db = get_db()
    session = get_session(session_id, student_id)
    session.hint_level = min(session.hint_level + 1, 2)
    db.commit()
    level = max(1, session.hint_level)
    q = db.query(Question).filter(Question.id == session.question_id).first()
    hint = _generate_hint(q, level) if q else None
    return {"hint_level": session.hint_level, "hint": hint or _HINT_FALLBACKS[level]}
