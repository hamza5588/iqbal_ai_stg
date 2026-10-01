"""Adapter: canonical MCQ candidates -> existing MCQQuestion DTO.

The downstream pipeline (question_bank_service.create_question, display
format QA, quiz save) already speaks :class:`MCQQuestion`. Rather than
change that surface (large blast radius), the new document-intelligence
path converts its output into the same DTO plus a parallel list of the
authoritative ``source_question_number`` values so the pipeline persists
the printed number instead of ``idx + 1``.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.services.quiz.document_parser.mcq_from_canonical import (
    MCQCandidate,
    MCQExtractionResult,
    apply_answer_key,
)
from app.services.quiz.models import MCQOption, MCQQuestion


def canonical_to_mcq_questions(
    result: MCQExtractionResult,
) -> Tuple[List[MCQQuestion], List[int], List[Optional[str]]]:
    """Return ``(mcq_questions, source_question_numbers, raw_answer_letters)``.

    - ``mcq_questions`` is what the existing pipeline persists.
    - ``source_question_numbers[i]`` is the printed number for
      ``mcq_questions[i]``. The pipeline MUST write this to
      ``AssessmentQuestion.source_question_number`` instead of ``i + 1``.
    - ``raw_answer_letters[i]`` is the letter from the answer key (or
      ``None`` when the key had no row for this question) so the pipeline
      can populate ``correct_answer_raw`` for review UIs.

    Only MCQs with exactly 4 options that carry an answer key are returned
    - the caller's validator already flagged the rest, so this converter
    stays a pure translation step (no filtering warnings inserted here).
    """
    answer_map: Dict[int, str] = apply_answer_key(result.mcqs, result.answer_key)

    mcqs_out: List[MCQQuestion] = []
    nums_out: List[int] = []
    letters_out: List[Optional[str]] = []

    for candidate in result.mcqs:
        if len(candidate.options) != 4:
            continue
        letter = answer_map.get(candidate.source_question_number)
        if letter is None:
            # No key entry -> caller decides whether this is fatal via the
            # validator; we skip here so the converter never invents an
            # answer (spec Step 9: LLM/converter must not fabricate keys).
            continue
        options = [
            MCQOption(label=opt.label, text=opt.text or "", latex=opt.latex)
            for opt in candidate.options
        ]
        mcq = MCQQuestion(
            question_text=candidate.stem or candidate.printed_label,
            question_latex=candidate.stem_latex,
            options=options,
            correct_option_label=letter,
            # Native/paddle extraction preserves the printed A-D order; the
            # answer key references those labels. Downstream shuffle must
            # NOT re-shuffle or the correct-index will drift again.
            preserve_option_order=True,
            conversion_confidence=0.95,
        )
        mcqs_out.append(mcq)
        nums_out.append(candidate.source_question_number)
        letters_out.append(letter)

    return mcqs_out, nums_out, letters_out
