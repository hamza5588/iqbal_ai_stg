"""Dual-evidence (text + page images) MCQ extraction for diagnostic/quiz PDFs.

Uses ``app.services.quiz.hybrid_vision_pipeline`` (in-app dual-evidence flow):
PDF pre-check → PyMuPDF text + high-res page renders → Qwen Vision/Groq →
structured MCQs → optional metadata enrichment.

Does not import root ``persor.py`` and does not touch the main Learning Chat path.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import List, Optional, Tuple

from app.services.quiz import hybrid_vision_pipeline as vision
from app.services.quiz.math_text import recover_fields
from app.services.quiz.models import MCQOption, MCQQuestion

logger = logging.getLogger(__name__)


def hybrid_vision_enabled() -> bool:
    """Hybrid vision is mandatory for PDF → MCQ extraction (no text-only fallback).

    ``QUIZ_HYBRID_VISION=false`` is ignored — PDF quizzes/diagnostics always use
    dual-evidence (text + page images) via ``hybrid_vision_pipeline``.
    """
    raw = (os.getenv("QUIZ_HYBRID_VISION") or "true").strip().lower()
    if raw in ("0", "false", "no", "off"):
        logger.warning(
            "QUIZ_HYBRID_VISION=%s is ignored; hybrid vision is always required.",
            raw,
        )
    return True


def _normalize_difficulty(raw: Optional[str]) -> Optional[str]:
    """Map PDF difficulty labels onto Question.difficulty enum; None if absent/unknown."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip().lower()
    if s in ("easy", "medium", "hard"):
        return s
    synonyms = {
        "e": "easy",
        "low": "easy",
        "simple": "easy",
        "m": "medium",
        "moderate": "medium",
        "avg": "medium",
        "average": "medium",
        "h": "hard",
        "high": "hard",
        "difficult": "hard",
        "challenging": "hard",
    }
    return synonyms.get(s)


def _option_label(raw: str, index: int) -> Optional[str]:
    label = (raw or "").strip().upper()[:1]
    if label in ("A", "B", "C", "D"):
        return label
    fallback = ("A", "B", "C", "D")
    if 0 <= index < 4:
        return fallback[index]
    return None


def vision_mcq_to_question(mcq) -> Optional[MCQQuestion]:
    """Convert one hybrid-vision ``MCQ`` into pipeline ``MCQQuestion`` (metadata optional)."""
    correct = (getattr(mcq, "correct_answer", None) or "").strip().upper()[:1]
    if correct not in ("A", "B", "C", "D"):
        return None

    mapped_opts: List[MCQOption] = []
    seen = set()
    for idx, opt in enumerate(list(getattr(mcq, "options", None) or [])):
        label = _option_label(getattr(opt, "label", "") or "", idx)
        if not label or label in seen:
            continue
        text, latex = recover_fields(getattr(opt, "text", "") or "", None)
        if not (text or "").strip() and not latex:
            continue
        mapped_opts.append(MCQOption(label=label, text=text or "", latex=latex))
        seen.add(label)
        if len(mapped_opts) >= 4:
            break

    if {o.label for o in mapped_opts} != {"A", "B", "C", "D"}:
        return None
    if correct not in {o.label for o in mapped_opts}:
        return None

    q_text, q_latex = recover_fields(getattr(mcq, "question", "") or "", None)
    if not (q_text or "").strip() and not q_latex:
        return None

    # Optional PDF metadata — use when present, skip when absent.
    topic = (getattr(mcq, "topic", None) or "").strip() or None
    domain = (getattr(mcq, "domain", None) or "").strip() or None
    cognitive = (getattr(mcq, "cognitive_level", None) or "").strip() or None
    concept_parts = [p for p in (topic, domain) if p]
    learning_concept = " / ".join(concept_parts) if concept_parts else None

    explanation_bits = []
    if cognitive:
        explanation_bits.append(f"Cognitive level: {cognitive}")
    if domain and domain not in (learning_concept or ""):
        explanation_bits.append(f"Domain: {domain}")
    explanation = "; ".join(explanation_bits) if explanation_bits else None

    return MCQQuestion(
        question_text=q_text or getattr(mcq, "question", ""),
        question_latex=q_latex,
        options=mapped_opts,
        correct_option_label=correct,  # type: ignore[arg-type]
        explanation=explanation,
        learning_concept=learning_concept,
        difficulty=_normalize_difficulty(getattr(mcq, "difficulty", None)),
        conversion_confidence=0.92,
        preserve_option_order=True,
    )


# Back-compat alias for earlier adapter name.
persor_mcq_to_question = vision_mcq_to_question


def extract_mcqs_hybrid_vision(
    pdf_bytes: bytes,
    *,
    require_answers: bool = True,
    progress_callback=None,
) -> Tuple[List[MCQQuestion], List[int], List[Optional[str]], List[str], float, Optional[str]]:
    """
    Run dual-evidence extraction on PDF bytes.

    Returns
    -------
    questions, source_question_numbers, answer_letters, warnings, confidence, title
    """
    if not pdf_bytes:
        raise ValueError("PDF bytes are empty")

    def _progress(step: str, pct: int, msg: str) -> None:
        if not progress_callback:
            return
        try:
            progress_callback(step, pct, msg)
        except Exception:  # noqa: BLE001
            pass

    _progress("extract", 64, "Hybrid vision: rendering pages + text evidence...")

    fd, temp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        with open(temp_path, "wb") as handle:
            handle.write(pdf_bytes)

        _progress("extract", 70, "Hybrid vision: Qwen analyzing text + page images...")
        result = vision.extract_mcqs(temp_path)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    warnings: List[str] = [
        f"Extractor: hybrid_vision ({vision.resolve_vision_model()})",
    ]

    questions: List[MCQQuestion] = []
    source_numbers: List[int] = []
    answer_letters: List[Optional[str]] = []
    skipped = 0

    for raw in list(getattr(result, "questions", None) or []):
        mapped = vision_mcq_to_question(raw)
        if mapped is None:
            skipped += 1
            qno = getattr(raw, "question_no", "?")
            ans = getattr(raw, "correct_answer", None)
            if require_answers and not ans:
                warnings.append(f"Q{qno}: skipped (no answer key letter)")
            else:
                warnings.append(f"Q{qno}: skipped (incomplete options/answer)")
            continue
        questions.append(mapped)
        try:
            source_numbers.append(int(getattr(raw, "question_no")))
        except (TypeError, ValueError):
            source_numbers.append(len(source_numbers) + 1)
        answer_letters.append(
            (getattr(raw, "correct_answer", None) or mapped.correct_option_label)
        )

    if skipped:
        warnings.append(f"Skipped {skipped} incomplete question(s) after hybrid extract.")

    if not questions:
        raise ValueError(
            "Hybrid vision extraction returned no usable MCQs "
            "(need stem, A-D options, and answer-key letter)."
        )

    title = (getattr(result, "title", None) or "").strip() or None
    confidence = max(0.7, 0.93 - 0.03 * skipped)
    _progress("extract", 78, f"Hybrid vision: {len(questions)} MCQ(s) ready")
    logger.info(
        "Hybrid vision extraction: %s question(s), title=%r, skipped=%s",
        len(questions),
        title,
        skipped,
    )
    return questions, source_numbers, answer_letters, warnings, confidence, title
