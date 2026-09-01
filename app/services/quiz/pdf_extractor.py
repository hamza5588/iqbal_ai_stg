"""AI-powered PDF Q&A extraction and intelligent pairing."""
from __future__ import annotations

import logging
import re
from typing import List, Optional, Union

from app.services.quiz.models import (
    ExtractedAnswer,
    ExtractedQuestion,
    PDFExtractionResult,
    PairingResult,
    QuestionAnswerPair,
)
from app.services.quiz.retry_utils import invoke_structured

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 80000

_EXTRACTION_PROMPT = """You are extracting questions and answers from an educational PDF document.

Rules:
- Extract ONLY content that appears in the document. Do NOT invent questions or answers.
- Handle varied layouts: separate Questions/Answers sections, inline Q&A, numbered lists, etc.
- Preserve math notation; put LaTeX in the latex fields when appropriate.
- For each question and answer, capture the number/label as shown (int or str).
- Extract EVERY numbered answer in the Answers section — do not stop early.
- When fractions or math are split across lines (e.g. "𝑥 = −" then "1/2"), merge them into one answer text.
- Include "Solution set: ..." lines as part of the answer text when present.
- Set format_detected to a short description (e.g. "separate_qa_sections", "inline_numbered").
- Set confidence 0-1 based on extraction clarity.
- Add warnings for ambiguous or partial extractions.

Document text:
{text}
"""


_PAIRING_PROMPT = """Match each extracted question to its correct answer from a PDF.

Rules:
- Use question/answer numbers when they align.
- When numbering differs, infer the best match from content.
- Set is_matched=false and low match_confidence for items you cannot pair.
- Do NOT invent answers — only pair extracted items.

Questions JSON:
{questions}

Answers JSON:
{answers}
"""


def _normalize_number(value: Union[int, str]) -> str:
    if isinstance(value, int):
        return str(value)
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def _prepare_pdf_text_for_extraction(pdf_text: str) -> str:
    """Clean RAG chunk text before LLM extraction."""
    text = pdf_text.strip()
    table_marker = "[Table data, extracted with row/column structure]"
    if table_marker in text:
        # Table re-extraction often duplicates and garbles Q&A numbering; prefer plain text.
        text = text.split(table_marker, 1)[0].strip()
    return text


def _valid_pairs(pairs: List[QuestionAnswerPair]) -> List[QuestionAnswerPair]:
    return [
        p
        for p in pairs
        if p.is_matched and p.question_text.strip() and p.answer_text.strip()
    ]


def _question_sort_key(pair: QuestionAnswerPair) -> tuple:
    num = pair.question_number
    if isinstance(num, int):
        return (0, num, "")
    normalized = _normalize_number(num)
    if normalized.isdigit():
        return (0, int(normalized), "")
    return (1, 0, normalized)


def _merge_pair_results(primary: PairingResult, secondary: PairingResult) -> PairingResult:
    """Keep reliable primary pairs; fill gaps from secondary without overwriting."""
    merged: dict[str, QuestionAnswerPair] = {}
    for pair in _valid_pairs(primary.pairs):
        merged[_normalize_number(pair.question_number)] = pair
    for pair in _valid_pairs(secondary.pairs):
        key = _normalize_number(pair.question_number)
        if key not in merged:
            merged[key] = pair
    warnings = list(dict.fromkeys(primary.warnings + secondary.warnings))
    ordered = sorted(merged.values(), key=_question_sort_key)
    return PairingResult(pairs=ordered, warnings=warnings)


def extract_qa_from_text(pdf_text: str) -> PDFExtractionResult:
    """Extract structured Q&A from raw PDF text using LLM structured output."""
    if not pdf_text or not pdf_text.strip():
        return PDFExtractionResult(
            confidence=0.0,
            warnings=["No text available for extraction"],
        )

    text = _prepare_pdf_text_for_extraction(pdf_text)[:_MAX_TEXT_CHARS]
    from app.utils.llm_factory import get_chat_model

    llm = get_chat_model(temperature=0.1, max_tokens=4096)
    result: PDFExtractionResult = invoke_structured(
        llm, PDFExtractionResult, _EXTRACTION_PROMPT.format(text=text)
    )
    if not result.warnings:
        result.warnings = []
    if not result.questions:
        result.warnings.append("No questions extracted")
    if not result.answers:
        result.warnings.append("No answers extracted")
    return result


def _deterministic_pair(
    questions: List[ExtractedQuestion],
    answers: List[ExtractedAnswer],
) -> PairingResult:
    """Pair Q&A by normalized number; collect unmatched items."""
    answer_by_num = {}
    for ans in answers:
        key = _normalize_number(ans.number)
        if key and key not in answer_by_num:
            answer_by_num[key] = ans

    pairs: List[QuestionAnswerPair] = []
    warnings: List[str] = []
    used_answer_keys = set()

    for q in questions:
        key = _normalize_number(q.number)
        ans = answer_by_num.get(key)
        if ans:
            used_answer_keys.add(key)
            pairs.append(
                QuestionAnswerPair(
                    question_number=q.number,
                    question_text=q.text.strip(),
                    question_latex=q.latex,
                    answer_text=ans.text.strip(),
                    answer_latex=ans.latex,
                    match_confidence=0.95,
                    is_matched=True,
                )
            )
        else:
            warnings.append(f"No answer found for question {q.number}")

    for ans in answers:
        key = _normalize_number(ans.number)
        if key not in used_answer_keys:
            warnings.append(f"Unmatched answer {ans.number}")

    return PairingResult(pairs=pairs, warnings=warnings)


def _llm_pair(
    questions: List[ExtractedQuestion],
    answers: List[ExtractedAnswer],
) -> PairingResult:
    """Use LLM to pair questions and answers when deterministic matching is incomplete."""
    import json

    from app.utils.llm_factory import get_chat_model

    prompt = _PAIRING_PROMPT.format(
        questions=json.dumps([q.model_dump() for q in questions], ensure_ascii=False),
        answers=json.dumps([a.model_dump() for a in answers], ensure_ascii=False),
    )
    llm = get_chat_model(temperature=0.0, max_tokens=4096)
    return invoke_structured(llm, PairingResult, prompt)


def pair_questions_answers(extraction: PDFExtractionResult) -> List[QuestionAnswerPair]:
    """Pair extracted questions to answers with confidence scores."""
    if not extraction.questions or not extraction.answers:
        return []

    result = _deterministic_pair(extraction.questions, extraction.answers)
    valid_count = len(_valid_pairs(result.pairs))

    if valid_count < len(extraction.questions):
        try:
            llm_result = _llm_pair(extraction.questions, extraction.answers)
            result = _merge_pair_results(result, llm_result)
        except Exception as exc:
            logger.warning("LLM pairing failed, using deterministic pairs: %s", exc)
            extraction.warnings.append(f"LLM pairing fallback failed: {exc}")

    extraction.warnings.extend(result.warnings)
    pairs = _valid_pairs(result.pairs)
    pairs.sort(key=_question_sort_key)
    if valid_count < len(extraction.questions) and pairs:
        missing = len(extraction.questions) - len(pairs)
        extraction.warnings.append(
            f"Matched {len(pairs)} of {len(extraction.questions)} questions ({missing} unmatched)"
        )
    return pairs
