"""Question bank service."""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from app.models.lms_models import Question
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.mcq_utils import (
    normalize_options,
    options_from_json,
    options_to_json,
    pick_display_fields,
    validate_mcq,
)
from app.utils.db import get_db

logger = logging.getLogger(__name__)


def _serialize_question(q: Question) -> dict:
    q_text, q_latex = pick_display_fields(q.question_text, q.question_latex)
    opts = []
    for opt in options_from_json(q.options_json):
        text, latex = pick_display_fields(opt.get("text"), opt.get("latex"))
        item = dict(opt)
        item["text"] = text
        item["latex"] = latex
        opts.append(item)
    return {
        "id": q.id,
        "topic_id": q.topic_id,
        "question_text": q_text or q.question_text,
        "question_latex": q_latex,
        "options": opts,
        "correct_option_index": q.correct_option_index,
        "correct_answer_raw": q.correct_answer_raw,
        "explanation": q.explanation,
        "difficulty": q.difficulty,
        "domain": q.domain,
        "topic_label": q.topic_label,
        "cognitive_level": q.cognitive_level,
        "source_type": q.source_type,
        "source_pdf_thread_id": q.source_pdf_thread_id,
        "source_question_number": q.source_question_number,
        "extraction_confidence": q.extraction_confidence,
        "is_active": q.is_active,
        "created_by": q.created_by,
    }


def _check_answer_key(
    question_text: str,
    question_latex: Optional[str],
    options: List[Any],
    correct_option_index: int,
    source_type: str,
) -> None:
    """Refuse a hand-typed question whose key the maths check disproves ("3+2=?" keyed "4").

    Keys read from a teacher's own PDF answer sheet are the teacher's call: those are only
    logged, never refused.
    """
    from app.services.quiz.mcq_math_verify import check_answer_key

    item = {
        "question_text": question_text,
        "question_latex": question_latex,
        "options": normalize_options(options),
        "correct_option_index": correct_option_index,
    }
    verdict = check_answer_key(item)
    if not verdict.disproved:
        return
    if source_type != "manual":
        logger.warning("Saving %s question with a disputed key (%s): %s", source_type, verdict.status, question_text[:80])
        return
    hint = ""
    if verdict.correct_indexes:
        hint = " The worked answer matches option " + ", ".join("ABCD"[i] for i in verdict.correct_indexes if i < 4) + "."
    raise LMSValidationError(
        {
            "wrong_key": "The marked correct option does not match the worked answer.",
            "no_correct_option": "None of the options is the correct answer.",
            "multiple_correct": "More than one option is correct.",
        }[verdict.status]
        + hint
    )


def create_question(
    created_by: int,
    question_text: str,
    options: List[Any],
    correct_option_index: int,
    topic_id: Optional[int] = None,
    question_latex: Optional[str] = None,
    correct_answer_raw: Optional[str] = None,
    explanation: Optional[str] = None,
    difficulty: str = "medium",
    source_type: str = "manual",
    source_pdf_thread_id: Optional[str] = None,
    source_question_number: Optional[int] = None,
    extraction_confidence: Optional[float] = None,
    domain: Optional[str] = None,
    topic_label: Optional[str] = None,
    cognitive_level: Optional[str] = None,
) -> Question:
    validate_mcq(options, correct_option_index)
    _check_answer_key(question_text, question_latex, options, correct_option_index, source_type)
    db = get_db()
    question = Question(
        topic_id=topic_id,
        created_by=created_by,
        question_text=question_text,
        question_latex=question_latex,
        options_json=options_to_json(options, correct_option_index),
        correct_option_index=correct_option_index,
        correct_answer_raw=correct_answer_raw,
        explanation=explanation,
        difficulty=difficulty,
        source_type=source_type,
        source_pdf_thread_id=source_pdf_thread_id,
        source_question_number=source_question_number,
        extraction_confidence=extraction_confidence,
        domain=domain.strip() if domain else None,
        topic_label=topic_label.strip() if topic_label else None,
        cognitive_level=cognitive_level.strip() if cognitive_level else None,
    )
    db.add(question)
    db.commit()
    db.refresh(question)
    return question


def get_question(question_id: int) -> Question:
    db = get_db()
    q = db.query(Question).filter(Question.id == question_id, Question.is_active.is_(True)).first()
    if not q:
        raise LMSNotFoundError(f"Question {question_id} not found")
    return q


def list_questions_by_topic(
    topic_id: int,
    limit: int = 100,
    offset: int = 0,
    difficulty: Optional[str] = None,
) -> List[Question]:
    db = get_db()
    q = db.query(Question).filter(Question.topic_id == topic_id, Question.is_active.is_(True))
    if difficulty:
        q = q.filter(Question.difficulty == difficulty)
    return q.order_by(Question.id.desc()).offset(offset).limit(limit).all()


def list_questions_by_source(source_type: str, limit: int = 100) -> List[Question]:
    db = get_db()
    return (
        db.query(Question)
        .filter(Question.source_type == source_type, Question.is_active.is_(True))
        .order_by(Question.id.desc())
        .limit(limit)
        .all()
    )


def update_question(question_id: int, **fields) -> Question:
    db = get_db()
    q = get_question(question_id)
    if "options" in fields and "correct_option_index" in fields:
        validate_mcq(fields["options"], fields["correct_option_index"])
        _check_answer_key(
            fields.get("question_text") or q.question_text,
            fields.get("question_latex") or q.question_latex,
            fields["options"],
            fields["correct_option_index"],
            q.source_type,
        )
        q.options_json = options_to_json(fields["options"], fields["correct_option_index"])
        q.correct_option_index = fields["correct_option_index"]
        fields.pop("options", None)
        fields.pop("correct_option_index", None)
    for key, val in fields.items():
        if hasattr(q, key) and val is not None:
            setattr(q, key, val)
    db.commit()
    db.refresh(q)
    return q


def soft_delete_question(question_id: int) -> None:
    db = get_db()
    q = get_question(question_id)
    q.is_active = False
    db.commit()


def question_to_dict(q: Question) -> dict:
    return _serialize_question(q)
