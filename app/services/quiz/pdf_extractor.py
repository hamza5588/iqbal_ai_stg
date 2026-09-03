"""AI-powered PDF Q&A extraction and intelligent pairing."""
from __future__ import annotations

import logging
import re
from typing import List, Optional, Union

from app.services.lms.mcq_utils import (
    harvest_answer_key,
    harvest_native_mcqs,
    is_label_only,
    parse_answer_label,
    split_stem_and_options,
)
from app.services.quiz.models import (
    ExtractedAnswer,
    ExtractedOption,
    ExtractedQuestion,
    LatexNormalizedBatch,
    PDFExtractionResult,
    PairingResult,
    QuestionAnswerPair,
)
from app.services.quiz.math_text import recover_fields
from app.services.quiz.retry_utils import invoke_structured

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 80000

_EXTRACTION_PROMPT = """You are extracting questions and answers from an educational PDF document.

Rules:
- Extract ONLY content that appears in the document. Do NOT invent questions or answers.
- Handle varied layouts: separate Questions/Answers sections, inline Q&A, numbered lists, native MCQs, etc.
- Preserve math notation; put LaTeX ONLY in latex fields. Text fields must be readable.
- Flattened PDF exponents (x2, a3b2) MUST be stored as latex x^{2}, a^{3}b^{2}. Never keep "4x2" or "a4b3".
- A stacked fraction (numerator line then denominator line) MUST become \\frac{num}{den}.
  Example: Simplify / (a^3 b^2)(a^2 b^4) / ab^3 → latex \\frac{(a^{3}b^{2})(a^{2}b^{4})}{ab^{3}}.
- Keep option order exactly as printed (A, B, C, D). Do not shuffle.
- For each question and answer, capture the number/label as shown (int or str).
- Extract EVERY numbered answer in the Answers / Answer Key / Answer Sheet section — do not stop early.
- When the PDF already has multiple-choice options, extract each option's FULL text into question.options.
  Never store only the letter (A/B/C/D). Do not prefix option text with "A." / "B)" — the label is separate.
- Question.text must be the stem only (no A/B/C/D lines).
- Answer-key letters (e.g. "1. B") go in answers as number + text "B".
- When fractions or math are split across lines (e.g. "𝑥 = −" then "1/2"), merge them into one answer text.
- Include "Solution set: ..." lines as part of the answer text when present.
- Set format_detected to a short description (e.g. "separate_qa_sections", "native_mcq_with_answer_key").
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


def _enrich_question_math(question: ExtractedQuestion) -> ExtractedQuestion:
    question.text, question.latex = recover_fields(question.text, question.latex)
    enriched: List[ExtractedOption] = []
    for opt in question.options or []:
        text, latex = recover_fields(opt.text, opt.latex)
        enriched.append(ExtractedOption(label=opt.label, text=text, latex=latex))
    question.options = enriched
    return question


def _options_from_extracted(question: ExtractedQuestion) -> List[ExtractedOption]:
    raw_opts = list(question.options or [])
    if len(raw_opts) == 4 and not any(is_label_only(o.text) for o in raw_opts):
        return raw_opts
    stem, harvested = split_stem_and_options(question.text or "")
    if harvested:
        question.text = stem
        return [
            ExtractedOption(label=o["label"], text=o["text"], latex=o.get("latex"))
            for o in harvested
        ]
    return [o for o in raw_opts if o.text and not is_label_only(o.text)]


def _postprocess_extraction(result: PDFExtractionResult, pdf_text: str) -> PDFExtractionResult:
    """Fill native MCQ options and merge a missed answer key without inventing content."""
    harvested_questions = harvest_native_mcqs(pdf_text)
    by_num = {_normalize_number(q.number): q for q in result.questions}
    for item in harvested_questions:
        key = _normalize_number(item["number"])
        opts = [
            ExtractedOption(label=o["label"], text=o["text"], latex=o.get("latex"))
            for o in item["options"]
        ]
        if key in by_num:
            q = by_num[key]
            if len(q.options) != 4 or any(is_label_only(o.text) for o in q.options):
                q.options = opts
            if item["text"] and (not (q.text or "").strip() or is_label_only(q.text)):
                q.text = item["text"]
            elif item["text"] and item["text"] not in (q.text or "") and len(item["text"]) > len(q.text or ""):
                q.text = item["text"]
        else:
            q = ExtractedQuestion(number=item["number"], text=item["text"], options=opts)
            result.questions.append(q)
            by_num[key] = q

    inline_answers = {
        _normalize_number(item["number"]): item.get("answer")
        for item in harvested_questions
        if item.get("answer")
    }

    for q in result.questions:
        q.options = _options_from_extracted(q)
        _enrich_question_math(q)

    existing = {_normalize_number(a.number) for a in result.answers}
    harvested = harvest_answer_key(pdf_text)
    harvested_map = {num: letter for num, letter in harvested}
    for ans in result.answers:
        key = _normalize_number(ans.number)
        if key in harvested_map:
            letter = harvested_map[key]
            if parse_answer_label(ans.text) != letter:
                ans.text = letter
            existing.add(key)
    added = 0
    for num, letter in harvested:
        key = _normalize_number(num)
        if key in existing:
            continue
        result.answers.append(ExtractedAnswer(number=int(num) if str(num).isdigit() else num, text=letter))
        existing.add(key)
        added += 1
    inline_added = 0
    for key, raw in inline_answers.items():
        if key in existing or not raw:
            continue
        number: Union[int, str] = int(key) if key.isdigit() else key
        result.answers.append(ExtractedAnswer(number=number, text=raw))
        existing.add(key)
        inline_added += 1
    if added:
        result.warnings.append(f"Merged {added} answers from answer-key section")
    if inline_added:
        result.warnings.append(f"Stored {inline_added} original inline answers (no key row)")
    elif harvested and not result.answers:
        result.warnings.append("Harvested answer key but numbers did not match questions")
    return result


def _correct_label_from_answer(answer_text: str) -> Optional[str]:
    label = parse_answer_label(answer_text)
    return label if label in ("A", "B", "C", "D") else None


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


_LATEX_NORMALIZE_PROMPT = """You reconstruct exam mathematics as LaTeX. Do NOT invent questions, options, or answers.
Do NOT shuffle options. Keep the same A/B/C/D order.

Input JSON was copied from a PDF. Exponents are often flattened (x2 means x^2, a3b2 means a^{3}b^{2}).
A fraction may appear as two lines under "Simplify:".

Rules:
- question.text = stem only (e.g. "Simplify:") — no A/B/C/D lines.
- question.latex = the full math using \\frac and ^{{ }}.
- Each option keeps its original label and meaning; fill option.latex with proper TeX.
- Never store only the letter A/B/C/D as option text.
- Example stem latex: \\frac{{(a^{{3}}b^{{2}})(a^{{2}}b^{{4}})}}{{ab^{{3}}}}
- Example option latex: 4x^{{2}}-7x   or   a^{{4}}b^{{3}}

Questions JSON:
{questions}
"""


def _llm_normalize_math(questions: List[ExtractedQuestion]) -> List[ExtractedQuestion]:
    """AI/Pydantic pass: reconstruct LaTeX while keeping harvest structure and option order."""
    if not questions:
        return questions
    import json

    from app.utils.llm_factory import get_chat_model

    llm = get_chat_model(temperature=0.0, max_tokens=4096)
    by_num = {_normalize_number(q.number): q for q in questions}
    batch_size = 6
    for i in range(0, len(questions), batch_size):
        chunk = questions[i : i + batch_size]
        payload = json.dumps([q.model_dump() for q in chunk], ensure_ascii=False)
        try:
            result: LatexNormalizedBatch = invoke_structured(
                llm, LatexNormalizedBatch, _LATEX_NORMALIZE_PROMPT.format(questions=payload)
            )
        except Exception as exc:
            logger.warning("LaTeX normalize batch failed: %s", exc)
            continue
        for neu in result.questions or []:
            key = _normalize_number(neu.number)
            orig = by_num.get(key)
            if not orig:
                continue
            orig.text, orig.latex = recover_fields(neu.text or orig.text, neu.latex or orig.latex)
            if not neu.options or len(neu.options) != 4:
                continue
            if any(is_label_only(o.text) for o in neu.options):
                continue
            label_map = {str(o.label or "").strip().upper()[:1]: o for o in neu.options}
            merged: List[ExtractedOption] = []
            for opt in orig.options:
                lab = str(opt.label or "").strip().upper()[:1]
                src = label_map.get(lab, opt)
                text, latex = recover_fields(src.text, src.latex)
                merged.append(ExtractedOption(label=lab, text=text, latex=latex))
            if len(merged) == 4:
                orig.options = merged
    return [by_num[_normalize_number(q.number)] for q in questions]


def _result_from_harvest(pdf_text: str) -> Optional[PDFExtractionResult]:
    """Numbered MCQ paper: harvest structure, then AI/Pydantic LaTeX reconstruction."""
    questions = harvest_native_mcqs(pdf_text)
    answers = harvest_answer_key(pdf_text)
    if len(questions) < 4:
        return None
    ans_map = {_normalize_number(num): letter for num, letter in answers}
    extracted_q: List[ExtractedQuestion] = []
    extracted_a: List[ExtractedAnswer] = []
    for item in questions:
        opts = [
            ExtractedOption(label=o["label"], text=o["text"], latex=o.get("latex"))
            for o in item["options"]
        ]
        q = ExtractedQuestion(
            number=item["number"],
            text=item["text"],
            latex=item.get("latex"),
            options=opts,
        )
        extracted_q.append(_enrich_question_math(q))
        letter = ans_map.get(_normalize_number(item["number"]))
        raw = letter or (item.get("answer") or "").strip()
        if raw:
            extracted_a.append(ExtractedAnswer(number=item["number"], text=raw))
    if len(extracted_a) < max(4, int(0.8 * len(extracted_q))):
        return None
    try:
        extracted_q = _llm_normalize_math(extracted_q)
    except Exception as exc:
        logger.warning("LaTeX normalize skipped: %s", exc)
    fmt = "native_mcq_with_answer_key" if answers else "native_mcq_with_inline_answers"
    return PDFExtractionResult(
        questions=extracted_q,
        answers=extracted_a,
        format_detected=fmt,
        confidence=0.94 if answers else 0.9,
        warnings=[],
    )


def extract_qa_from_text(pdf_text: str) -> PDFExtractionResult:
    """Extract structured Q&A from raw PDF text using LLM structured output."""
    if not pdf_text or not pdf_text.strip():
        return PDFExtractionResult(
            confidence=0.0,
            warnings=["No text available for extraction"],
        )

    text = _prepare_pdf_text_for_extraction(pdf_text)[:_MAX_TEXT_CHARS]
    harvested = _result_from_harvest(text)
    if harvested:
        logger.info(
            "Native MCQ harvest + LaTeX normalize: %s questions, %s answers",
            len(harvested.questions),
            len(harvested.answers),
        )
        return harvested
    from app.utils.llm_factory import get_chat_model

    llm = get_chat_model(temperature=0.1, max_tokens=4096)
    result: PDFExtractionResult = invoke_structured(
        llm, PDFExtractionResult, _EXTRACTION_PROMPT.format(text=text)
    )
    if not result.warnings:
        result.warnings = []
    result = _postprocess_extraction(result, text)
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
                    options=list(q.options or []),
                    correct_option_label=_correct_label_from_answer(ans.text),
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
    q_by_num = {_normalize_number(q.number): q for q in extraction.questions}
    for pair in pairs:
        source = q_by_num.get(_normalize_number(pair.question_number))
        if source and source.options and not pair.options:
            pair.options = list(source.options)
        if not pair.correct_option_label:
            pair.correct_option_label = _correct_label_from_answer(pair.answer_text)
    pairs.sort(key=_question_sort_key)
    if valid_count < len(extraction.questions) and pairs:
        missing = len(extraction.questions) - len(pairs)
        extraction.warnings.append(
            f"Matched {len(pairs)} of {len(extraction.questions)} questions ({missing} unmatched)"
        )
    return pairs
