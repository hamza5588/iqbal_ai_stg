"""Convert Q+A pairs to validated 4-option MCQs."""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import List, Optional

from pydantic import ValidationError

from app.services.lms.mcq_utils import shuffle_options
from app.services.quiz.models import MCQBatchResult, MCQQuestion, QuestionAnswerPair
from app.services.quiz.retry_utils import format_validation_errors, invoke_structured, retry_on_validation_error
from app.utils.llm_factory import get_chat_model

logger = logging.getLogger(__name__)

_MCQ_PROMPT = """Convert this question and its correct answer into a 4-option multiple choice question.

Rules:
- The PDF answer MUST appear verbatim (or equivalent math) as one of the 4 options.
- Generate exactly 3 plausible wrong distractors (common mistakes for math).
- All 4 options must be unique. No "all of the above" or "none of the above".
- Assign labels A, B, C, D to options.
- Set correct_option_label to the label of the option matching the PDF answer.
- Preserve math in latex fields when needed for MathJax rendering.
- Set conversion_confidence 0-1.

Question: {question}
Question LaTeX: {question_latex}
Correct answer from PDF: {answer}
Answer LaTeX: {answer_latex}
{retry_hint}
"""


def _build_prompt(pair: QuestionAnswerPair, retry_hint: str = "") -> str:
    return _MCQ_PROMPT.format(
        question=pair.question_text,
        question_latex=pair.question_latex or "",
        answer=pair.answer_text,
        answer_latex=pair.answer_latex or "",
        retry_hint=retry_hint,
    )


def _normalize_match_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"solution set:.*", "", text, flags=re.I | re.DOTALL)
    text = re.sub(r"[^\w\s+\-=/.,]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _answer_matches_options(mcq: MCQQuestion, pair: QuestionAnswerPair) -> bool:
    answer_parts = [
        _normalize_match_text(pair.answer_text),
        _normalize_match_text(pair.answer_latex or ""),
    ]
    answer_parts = [p.split("\n")[0].strip() for p in answer_parts if p]
    if not answer_parts:
        return True

    option_parts: list[str] = []
    for opt in mcq.options:
        option_parts.append(_normalize_match_text(opt.text))
        if opt.latex:
            option_parts.append(_normalize_match_text(opt.latex))
    option_parts = [p for p in option_parts if p]

    for answer in answer_parts:
        for option in option_parts:
            if answer in option or option in answer:
                return True
            answer_tokens = set(re.findall(r"[\w]+", answer))
            option_tokens = set(re.findall(r"[\w]+", option))
            if answer_tokens and len(answer_tokens & option_tokens) >= max(2, len(answer_tokens) // 2):
                return True
    return False


def _validate_pdf_answer_in_options(mcq: MCQQuestion, pair: QuestionAnswerPair) -> MCQQuestion:
    if not _answer_matches_options(mcq, pair):
        raise ValueError("PDF answer not found in MCQ options")
    return mcq


def convert_pair_to_mcq(pair: QuestionAnswerPair) -> MCQQuestion:
    """Convert a single Q+A pair to a validated MCQ using structured LLM output."""
    llm = get_chat_model(temperature=0.3, max_tokens=2048)
    retry_hint = ""

    def _invoke() -> MCQQuestion:
        mcq: MCQQuestion = invoke_structured(
            llm, MCQQuestion, _build_prompt(pair, retry_hint)
        )
        return _validate_pdf_answer_in_options(mcq, pair)

    def _on_retry(exc: ValidationError | ValueError, attempt: int) -> None:
        nonlocal retry_hint
        retry_hint = (
            f"\nPrevious attempt {attempt} failed validation: "
            f"{format_validation_errors(exc) if isinstance(exc, ValidationError) else str(exc)}. Fix these issues."
        )

    return retry_on_validation_error(_invoke, max_retries=2, on_retry=_on_retry)


def convert_pairs_batch(
    pairs: List[QuestionAnswerPair],
    quiz_title: Optional[str] = None,
) -> MCQBatchResult:
    """Convert multiple pairs; collect failures without stopping the batch."""
    questions: List[MCQQuestion] = []
    failed: List[str] = []

    for pair in pairs:
        try:
            mcq = convert_pair_to_mcq(pair)
            questions.append(mcq)
        except (ValidationError, ValueError, Exception) as exc:
            label = f"Q{pair.question_number}"
            failed.append(f"{label}: {exc}")
            logger.warning("MCQ conversion failed for %s: %s", label, exc)

    return MCQBatchResult(quiz_title=quiz_title, questions=questions, failed_conversions=failed)


def mcq_to_question_fields(mcq: MCQQuestion) -> dict:
    """Map MCQQuestion to question_bank_service fields with shuffled options."""
    opts = [{"label": o.label, "text": o.text, "latex": o.latex} for o in mcq.options]
    correct_idx = next(i for i, o in enumerate(mcq.options) if o.label == mcq.correct_option_label)
    shuffled, new_correct = shuffle_options(opts, correct_idx)
    return {
        "question_text": mcq.question_text,
        "question_latex": mcq.question_latex,
        "options": shuffled,
        "correct_option_index": new_correct,
        "explanation": mcq.explanation,
        "extraction_confidence": mcq.conversion_confidence,
    }
