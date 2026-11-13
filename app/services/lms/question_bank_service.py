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

# AI conversion_confidence below this → Needs review (same bar as diagnostic PDF).
_AI_AUTO_VERIFY_CONFIDENCE_MIN = 0.85


def quiz_question_review(q: Question) -> dict:
    """Target-PDF-style status from the AI's own conversion confidence — not rule checks.

    High AI confidence → ``auto_verified``; low / missing confidence → ``needs_review``.
    """
    conf = q.extraction_confidence
    if conf is None:
        return {
            "verification_status": "needs_review",
            "blocking": ["ai_confidence: missing"],
            "extraction_confidence": None,
        }
    score = float(conf)
    if score >= _AI_AUTO_VERIFY_CONFIDENCE_MIN:
        return {
            "verification_status": "auto_verified",
            "blocking": [],
            "extraction_confidence": score,
        }
    return {
        "verification_status": "needs_review",
        "blocking": [f"ai_confidence: {score:.2f}"],
        "extraction_confidence": score,
    }


def _serialize_question(q: Question) -> dict:
    q_text, q_latex = pick_display_fields(q.question_text, q.question_latex)
    opts = []
    for opt in options_from_json(q.options_json):
        text, latex = pick_display_fields(opt.get("text"), opt.get("latex"))
        item = dict(opt)
        item["text"] = text
        item["latex"] = latex
        opts.append(item)
    review = quiz_question_review(q)
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
        "verification_status": review["verification_status"],
        "verification_blocking": review["blocking"],
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
    allow_duplicate_option_texts: bool = False,
    cognitive_level: Optional[str] = None,
    printed_difficulty: Optional[str] = None,
    domain: Optional[str] = None,
    topic_label: Optional[str] = None,
) -> Question:
    from app.services.lms import question_attributes as qa

    validate_mcq(
        options,
        correct_option_index,
        allow_duplicate_texts=allow_duplicate_option_texts,
    )
    _check_answer_key(question_text, question_latex, options, correct_option_index, source_type)
    db = get_db()
    question = Question(
        topic_id=topic_id,
        created_by=created_by,
        question_text=question_text,
        question_latex=question_latex,
        options_json=options_to_json(
            options,
            correct_option_index,
            allow_duplicate_texts=allow_duplicate_option_texts,
        ),
        correct_option_index=correct_option_index,
        correct_answer_raw=correct_answer_raw,
        explanation=explanation,
        difficulty=difficulty,
        cognitive_level=qa.normalize_cognitive_level(cognitive_level) or qa.cognitive_from_explanation(explanation),
        printed_difficulty=(printed_difficulty or "").strip()[:32] or None,
        source_type=source_type,
        source_pdf_thread_id=source_pdf_thread_id,
        source_question_number=source_question_number,
        extraction_confidence=extraction_confidence,
        domain=domain.strip() if domain else None,
        topic_label=topic_label.strip() if topic_label else None,
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
    allow_dup = bool(fields.pop("allow_duplicate_option_texts", False))
    # PDF-harvested items may keep paper duplicates (Target bank style); generated stay unique.
    if q.source_type in {"pdf_qa_converted", "pdf_ai", "mixed"}:
        allow_dup = True
    if "options" in fields and "correct_option_index" in fields:
        validate_mcq(
            fields["options"],
            fields["correct_option_index"],
            allow_duplicate_texts=allow_dup,
        )
        _check_answer_key(
            fields.get("question_text") or q.question_text,
            fields.get("question_latex") or q.question_latex,
            fields["options"],
            fields["correct_option_index"],
            q.source_type,
        )
        q.options_json = options_to_json(
            fields["options"],
            fields["correct_option_index"],
            allow_duplicate_texts=allow_dup,
        )
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


def ai_edit_question(question_id: int, instruction: str, *, user_id: Optional[int] = None) -> dict:
    """Rewrite a quiz/diagnostic MCQ from a short plain-language admin/teacher prompt.

    Same approach as Target PDF bank AI edit: no math keyboard — the model restores
    flattened radicals/fractions with ``$...$`` LaTeX and updates the stored MCQ.
    """
    import json

    from pydantic import BaseModel, Field

    from app.services.quiz.retry_utils import invoke_structured
    from app.utils.llm_factory import get_chat_model
    from app.utils.llm_gateway import llm_workflow

    instruction = (instruction or "").strip()
    if len(instruction) < 3:
        raise LMSValidationError("Describe what to fix (e.g. 'put n as the index of the radical').")
    if len(instruction) > 2000:
        raise LMSValidationError("Instruction is too long (max 2000 characters).")

    q = get_question(question_id)
    options = options_from_json(q.options_json)
    labels = "ABCD"

    class _AiMcqEdit(BaseModel):
        question_text: str = Field(..., description="Full stem; math in $...$ LaTeX when needed")
        options: List[str] = Field(..., description="Exactly four option texts in A–D order")
        correct_label: str = Field(..., description="A, B, C, or D")
        conversion_confidence: float = Field(
            ...,
            ge=0.0,
            le=1.0,
            description="Your confidence 0–1 that the edited MCQ is correct and ready",
        )
        notes: Optional[str] = Field(None, description="One-line summary of the change")

    current = {
        "question_text": q.question_text,
        "options": [o.get("text") for o in options],
        "correct_label": labels[q.correct_option_index]
        if q.correct_option_index is not None and 0 <= q.correct_option_index < 4
        else "A",
        "extraction_confidence": q.extraction_confidence,
    }

    prompt = (
        "You are editing ONE multiple-choice math question for a school quiz.\n"
        "The teacher instruction describes what is wrong. Fix ONLY what they ask for.\n"
        "Keep the same meaning, answer key, and four options unless the instruction says otherwise.\n"
        "Math rules:\n"
        "- Prefer clear LaTeX inside single $...$ for radicals, fractions, exponents, subscripts.\n"
        "- Nth root with index n of a must be $\\sqrt[n]{a}$ (index on the radical), "
        "never plain '√a n' or '√an'.\n"
        "- Square root of a: $\\sqrt{a}$. Fraction a/b: $\\frac{a}{b}$.\n"
        "- Keep surrounding English prose as plain text outside the $...$ spans.\n"
        "- correct_label must be A, B, C, or D.\n"
        "- options must be exactly four non-empty strings in order A–D.\n"
        "- Prefer four distinct options; if the paper truly has identical distractors, keep them.\n"
        "- Set conversion_confidence 0–1 for how sure you are the edited item is correct.\n"
        f"  Use >= {_AI_AUTO_VERIFY_CONFIDENCE_MIN} only when stem, options, and key look solid.\n\n"
        f"TEACHER INSTRUCTION:\n{instruction}\n\n"
        f"CURRENT ITEM JSON:\n{json.dumps(current, ensure_ascii=False)}\n"
    )

    with llm_workflow("quiz_question_ai_edit", user_id=user_id, user_role="teacher"):
        llm = get_chat_model(temperature=0.2, max_tokens=2500)
        draft: _AiMcqEdit = invoke_structured(llm, _AiMcqEdit, prompt)

    opts = [str(o).strip() for o in draft.options]
    if len(opts) != 4 or not all(opts):
        raise LMSValidationError("AI returned invalid options; try again with a clearer instruction.")
    label = (draft.correct_label or "").strip().upper()
    if label not in labels:
        raise LMSValidationError("AI returned an invalid correct answer; try again.")
    correct_idx = labels.index(label)
    stem = (draft.question_text or "").strip()
    if len(stem) < 3:
        raise LMSValidationError("AI returned an empty question; try again.")

    option_payload = [{"label": labels[i], "text": opts[i], "latex": None} for i in range(4)]
    # Same as Target PDF bank correct_item: do not hard-fail on duplicate option text.
    updated = update_question(
        question_id,
        question_text=stem,
        question_latex=None,
        options=option_payload,
        correct_option_index=correct_idx,
        extraction_confidence=float(draft.conversion_confidence),
        allow_duplicate_option_texts=True,
    )
    return {
        "applied": True,
        "notes": (draft.notes or "").strip() or None,
        "question": question_to_dict(updated),
    }
