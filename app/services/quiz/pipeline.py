"""End-to-end PDF → MCQ quiz pipeline."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable, List, Optional

from app.models.lms_models import AssessmentQuestion, QuizPdfSource
from app.services.lms import assessment_service, question_bank_service
from app.services.quiz.assessment_doc_validation import (
    AssessmentDocValidationError,
    assert_nonempty_extracted_text,
    assert_topic_or_key_area_available,
    assert_valid_extraction,
    assert_valid_mcqs,
    assert_valid_pairs,
    public_error_message,
)
from app.services.quiz.diagnostic_generator import generate_mcqs_from_content
from app.services.quiz.mcq_converter import convert_pairs_batch, mcq_to_question_fields
from app.services.quiz.models import MCQQuestion
from app.services.quiz.section_topics import (
    parse_section_topics,
    question_number_from_label,
    topic_for_question_number,
)
from app.services.quiz.hybrid_mcq_extractor import (
    extract_mcqs_hybrid_vision,
    hybrid_vision_enabled,
)
from app.services.quiz.pdf_extractor import extract_qa_from_text, pair_questions_answers
from app.services.quiz.thread_text import get_thread_full_text
from app.utils.db import get_db

logger = logging.getLogger(__name__)

_MAX_CONTENT_MCQS = 40
ProgressCallback = Optional[Callable[[str, int, str], None]]


def _run_hybrid_vision_extraction(
    pdf_bytes: bytes,
    *,
    is_diagnostic: bool,
    wanted: Optional[int],
    progress_callback: ProgressCallback,
) -> tuple[List[MCQQuestion], List[int], List[Optional[str]], List[str], float, Optional[str]]:
    """PDF bytes → dual-evidence (text + page images) → structured MCQs."""
    questions, source_numbers, answer_letters, warnings, confidence, title = (
        extract_mcqs_hybrid_vision(
            pdf_bytes,
            require_answers=is_diagnostic,
            progress_callback=progress_callback,
        )
    )
    if wanted is not None and len(questions) > wanted:
        questions = questions[:wanted]
        source_numbers = source_numbers[:wanted]
        answer_letters = answer_letters[:wanted]
        warnings.append(f"Using first {wanted} of hybrid-extracted questions")
    return questions, source_numbers, answer_letters, warnings, confidence, title


class _HybridExtractionShim:
    """Duck-type for pipeline confidence / title lift after hybrid vision."""

    def __init__(
        self,
        *,
        title: Optional[str],
        confidence: float,
        warnings: List[str],
    ):
        self.title = title
        self.confidence = confidence
        self._warnings = list(warnings)

    def model_dump(self):
        return {
            "parser": "hybrid_vision",
            "warnings": self._warnings,
            "confidence": self.confidence,
            "title": self.title,
        }


def _set_pdf_source_status(
    source_id: int,
    status: str,
    error_message: Optional[str] = None,
    overall_confidence: Optional[float] = None,
) -> None:
    db = get_db()
    source = db.query(QuizPdfSource).filter(QuizPdfSource.id == source_id).first()
    if not source:
        return
    source.extraction_status = status
    source.error_message = error_message
    if overall_confidence is not None:
        source.overall_confidence = overall_confidence
    db.commit()


def _report_progress(
    callback: ProgressCallback,
    step: str,
    progress: int,
    message: str,
) -> None:
    if not callback:
        return
    try:
        callback(step, int(max(0, min(100, progress))), message)
    except Exception as exc:  # noqa: BLE001 - never fail pipeline on progress UI
        logger.debug("Quiz progress callback failed: %s", exc)


def _clamp_question_count(value: Optional[int], default: int = 10) -> int:
    try:
        n = int(value) if value is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(_MAX_CONTENT_MCQS, n))


def _looks_like_qa_document(text: str) -> bool:
    """Cheap heuristic — skip expensive Q&A LLM extract when PDF is plain notes."""
    sample = (text or "")[:14000]
    if not sample.strip():
        return False
    has_answer_section = bool(
        re.search(r"(?i)\b(answer\s*key|answers?\s*:|answer\s*sheet|solutions?\s*:)\b", sample)
    )
    has_numbered_q = bool(
        re.search(r"(?im)^\s*(?:q(?:uestion)?\s*\.?\s*)?\d{1,3}[\).\:\-]\s+\S", sample)
    )
    has_mcq_opts = bool(re.search(r"(?im)^\s*[A-D][\).]\s+\S.{2,}", sample))
    return bool(has_answer_section or (has_numbered_q and has_mcq_opts))


def _generate_mcqs_from_any_pdf(
    text: str,
    topic: str,
    count: int,
    exclude_question_texts: Optional[List[str]] = None,
) -> List[MCQQuestion]:
    """Generate N MCQs from general PDF content (batched; works without an answer key)."""
    content = (text or "").strip()
    if not content:
        raise ValueError("No readable text found in the PDF")
    # Keep prompt size manageable while still covering the document.
    snippet = content[:14000]
    wanted = _clamp_question_count(count)
    out: List[MCQQuestion] = []
    exclude: List[str] = list(exclude_question_texts or [])
    empty_streak = 0
    # Allow retries when the model returns fewer than requested (common for N>8).
    max_attempts = max(4, ((wanted + 9) // 10) + 3)
    attempts = 0
    while len(out) < wanted and attempts < max_attempts:
        attempts += 1
        batch_n = min(10, wanted - len(out))
        batch = generate_mcqs_from_content(
            snippet,
            topic or "General",
            batch_n,
            exclude_question_texts=exclude,
        )
        if not batch:
            empty_streak += 1
            if empty_streak >= 2:
                break
            continue
        empty_streak = 0
        seen = {e.strip().lower() for e in exclude if e}
        added = 0
        for q in batch:
            key = (q.question_text or "")[:200].strip().lower()
            if key and key in seen:
                continue
            out.append(q)
            if key:
                exclude.append((q.question_text or "")[:200])
                seen.add(key)
            added += 1
            if len(out) >= wanted:
                break
        if added == 0:
            empty_streak += 1
            if empty_streak >= 2:
                break
    if len(out) < wanted:
        logger.warning(
            "Content MCQ generation short: got %s of %s after %s attempts",
            len(out),
            wanted,
            attempts,
        )
    return out[:wanted]


def _canonical_parser_enabled() -> bool:
    """True when the canonical document-parser path should run.

    Phase 3 flipped this ON by default: layout-aware extraction is now the
    primary path, and the legacy RAG-text + regex extractor is only used
    when explicitly requested via ``QUIZ_DOCUMENT_PARSER=legacy``. This
    matches spec Step 9 ("LLM extraction must no longer be the primary PDF
    parser") and Step 25 ("The final production path must not depend on
    the old regex recovery approach").

    Selectable values (env var ``QUIZ_DOCUMENT_PARSER``):
        - unset / empty    -> canonical path with the ``native`` parser
        - ``native``       -> canonical path with PyMuPDF (default engine)
        - ``paddle``       -> canonical path with PP-StructureV3
        - ``legacy``       -> old RAG-text + LLM Q&A extractor (opt-out)
    """
    value = (os.environ.get("QUIZ_DOCUMENT_PARSER") or "").strip().lower()
    return value != "legacy"


def _run_canonical_extraction(
    pdf_bytes: bytes,
    *,
    assessment_id: int,
    is_diagnostic: bool,
    wanted: Optional[int],
    progress_callback: ProgressCallback,
) -> tuple[List[MCQQuestion], List[int], List[Optional[str]], List[str], float, str]:
    """Run PDF -> CanonicalDocument -> MCQCandidates -> MCQQuestion.

    Returns ``(batch_questions, source_question_numbers, raw_answer_letters,
    warnings, extraction_confidence, parser_name)``.

    Raises :class:`AssessmentDocValidationError` if the validator reports a
    fatal issue (missing key, wrong option shape, duplicate numbering...).
    Never returns a partial result silently - that class of "quietly
    downgrade to LLM" behavior is exactly what the new architecture removes.
    """
    from app.services.quiz.document_parser import (
        DocumentParserError,
        get_document_parser,
    )
    from app.services.quiz.document_parser.mcq_from_canonical import parse_mcqs
    from app.services.quiz.document_parser.to_mcq_question import (
        canonical_to_mcq_questions,
    )
    from app.services.quiz.document_parser.validator import validate_extraction

    _report_progress(progress_callback, "extract", 65, "Layout-aware PDF parsing...")
    try:
        parser = get_document_parser()
    except DocumentParserError as exc:
        raise AssessmentDocValidationError(
            detail=f"Document parser unavailable: {exc}"
        ) from exc

    try:
        canonical = parser.parse(pdf_bytes, document_id=str(assessment_id))
    except DocumentParserError as exc:
        raise AssessmentDocValidationError(
            detail=f"Document parser failed: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Canonical parser %s raised unexpectedly", parser.name)
        raise AssessmentDocValidationError(
            detail=f"Document parser crashed: {exc}"
        ) from exc

    _report_progress(progress_callback, "extract", 75, "Extracting MCQs...")
    extraction = parse_mcqs(canonical)

    # Validation happens in two stages so partial-document issues drop the
    # affected questions instead of killing the whole upload:
    #
    #   stage A - per-question option-shape issues (one question missing a
    #       stem/option because of a multi-column layout glitch) are
    #       demoted to warnings. The ``canonical_to_mcq_questions`` filter
    #       drops those from the save batch, so bad data cannot reach the
    #       DB. This matches "fail loud only on things that would corrupt
    #       the quiz, not on things we can safely skip".
    #   stage B - whole-document failures (missing answer-key entirely,
    #       duplicate numbers, zero questions) still raise and surface a
    #       clear error to the uploader. Those cannot be worked around.
    _SKIPPABLE = {"wrong_option_count", "wrong_option_order", "empty_option"}
    report = validate_extraction(
        extraction,
        require_answer_key=is_diagnostic,
        expect_min_questions=1,
    )
    fatal_errors = [i for i in report.errors if i.code not in _SKIPPABLE]
    skipped_errors = [i for i in report.errors if i.code in _SKIPPABLE]
    if fatal_errors:
        detail = "; ".join(i.message for i in fatal_errors[:3])
        raise AssessmentDocValidationError(
            detail=f"PDF extraction validation failed: {detail}"
        )

    batch_questions, source_numbers, raw_letters = canonical_to_mcq_questions(
        extraction
    )
    if not batch_questions:
        raise AssessmentDocValidationError(
            detail=(
                "The document parser could not build any MCQs with matching "
                "answer-key entries. Verify the answer key is present in the PDF."
            )
        )
    # Diagnostic uploads expect roughly the full question count. If a large
    # fraction of the paper got dropped by the per-question filter above,
    # that is itself a systemic failure the uploader must see.
    total_parsed = len(extraction.mcqs)
    if total_parsed and len(batch_questions) < max(1, int(0.5 * total_parsed)):
        raise AssessmentDocValidationError(
            detail=(
                f"Only {len(batch_questions)} of {total_parsed} questions could "
                f"be extracted cleanly. Please re-upload a clearer PDF."
            )
        )
    if wanted is not None and len(batch_questions) > wanted:
        batch_questions = batch_questions[:wanted]
        source_numbers = source_numbers[:wanted]
        raw_letters = raw_letters[:wanted]

    warnings: List[str] = [
        f"Parser: {parser.name} ({parser.version})",
    ]
    warnings.extend(i.message for i in report.warnings)
    # Demoted per-question option-shape errors show up here too, so an
    # operator reviewing the quiz can see which printed questions got
    # dropped and why (e.g. "Question 22 has 5 options (expected 4)").
    warnings.extend(f"[skipped] {i.message}" for i in skipped_errors)
    if skipped_errors:
        warnings.append(
            f"Skipped {len(skipped_errors)} questions with option-shape issues; "
            f"kept {len(batch_questions)} of {total_parsed} parsed."
        )
    warnings.extend(canonical.warnings)
    warnings.extend(extraction.warnings)
    # Simple confidence proxy: 0.9 base minus 0.05 per warning, floored at 0.7.
    confidence = max(0.7, 0.9 - 0.05 * (len(report.warnings) + len(skipped_errors)))
    return (
        batch_questions,
        source_numbers,
        raw_letters,
        warnings,
        confidence,
        parser.name,
    )


def run_pdf_quiz_pipeline(
    assessment_id: int,
    rag_thread_id: str,
    user_id: int,
    topic_id: Optional[int] = None,
    pdf_text: Optional[str] = None,
    question_count: Optional[int] = None,
    progress_callback: ProgressCallback = None,
    pdf_bytes: Optional[bytes] = None,
) -> dict:
    """
    Build MCQs from a PDF.

    Diagnostics require a valid Q&A / answer-key layout (strict validation).
    Quizzes prefer that layout when present; otherwise generate the requested
    number of MCQs from general PDF content.
    """
    assessment = assessment_service.get_assessment(assessment_id)
    source = assessment_service.link_pdf_source(
        assessment_id,
        rag_thread_id=rag_thread_id,
        original_filename=getattr(assessment, "title", None),
    )
    _set_pdf_source_status(source.id, "processing")
    _report_progress(progress_callback, "extract", 62, "Reading PDF content...")

    # None = keep all Q&A pairs (diagnostic path). Quiz UI always passes a count.
    wanted: Optional[int] = None
    if question_count is not None:
        wanted = _clamp_question_count(question_count)

    is_diagnostic = assessment.assessment_type == "diagnostic"

    try:
        text = pdf_text or get_thread_full_text(rag_thread_id, user_id)
        # Hybrid vision can read page images even when the text layer is empty
        # (scanned PDFs). Only require extractable text when we cannot try vision.
        if not (text or "").strip():
            if not (pdf_bytes and hybrid_vision_enabled()):
                assert_nonempty_extracted_text(text)
            text = text or ""
        else:
            assert_nonempty_extracted_text(text)

        creation_mode = "pdf_qa_auto"
        source_type = "pdf_qa_converted"
        pairs = []
        extraction = None
        batch_questions: List[MCQQuestion] = []
        failed_conversions: List[str] = []
        warnings: List[str] = []
        pair_answer_texts: List[Optional[str]] = []
        # Authoritative source_question_number list. When the canonical
        # parser owns the run it fills this with the PRINTED numbers from
        # the PDF; otherwise it stays empty and the pipeline falls back to
        # the legacy 1-based index (kept for behavior parity while the new
        # path is opt-in).
        canonical_source_numbers: List[int] = []
        canonical_parser_name: Optional[str] = None

        if not (text or "").strip() and pdf_bytes and hybrid_vision_enabled():
            warnings.append(
                "No extractable PDF text layer; relying on hybrid vision page images."
            )

        # Preferred path: dual-evidence hybrid vision (persor.py flow) —
        # text + page images → Qwen/Groq → structured MCQs. Preserves math
        # better than text-only loaders. Falls back to canonical/legacy on
        # failure (diagnostics still fail loud if no later path succeeds).
        run_hybrid = bool(pdf_bytes) and hybrid_vision_enabled() and (
            is_diagnostic or _looks_like_qa_document(text) or not (text or "").strip()
        )
        if run_hybrid:
            try:
                (
                    batch_questions,
                    canonical_source_numbers,
                    pair_answer_texts,
                    hybrid_warnings,
                    hybrid_conf,
                    hybrid_title,
                ) = _run_hybrid_vision_extraction(
                    pdf_bytes,
                    is_diagnostic=is_diagnostic,
                    wanted=wanted,
                    progress_callback=progress_callback,
                )
                creation_mode = "pdf_qa_auto"
                source_type = "pdf_qa_converted"
                warnings.extend(hybrid_warnings)
                extraction = _HybridExtractionShim(
                    title=hybrid_title,
                    confidence=hybrid_conf,
                    warnings=hybrid_warnings,
                )
            except Exception as hybrid_exc:  # noqa: BLE001 - fall through
                logger.warning(
                    "Hybrid vision extraction failed for assessment %s: %s",
                    assessment_id,
                    hybrid_exc,
                )
                warnings.append(
                    f"Hybrid vision extraction skipped: {hybrid_exc}"
                )
                batch_questions = []
                canonical_source_numbers = []
                pair_answer_texts = []
                extraction = None

        # Document-intelligence path (native/paddle). Runs when hybrid did
        # not produce questions. Skipped when disabled or when no PDF bytes.
        #
        # Diagnostics always try it and fail loud. Quizzes only try it when
        # the PDF looks like a Q&A / answer-key paper - a lesson-notes PDF has
        # no printed questions to preserve, so it goes straight to the
        # content-generation path below.
        run_canonical = (
            not batch_questions
            and bool(pdf_bytes)
            and _canonical_parser_enabled()
            and (is_diagnostic or _looks_like_qa_document(text))
        )
        if run_canonical:
            try:
                (
                    batch_questions,
                    canonical_source_numbers,
                    pair_answer_texts,
                    canon_warnings,
                    canon_conf,
                    canonical_parser_name,
                ) = _run_canonical_extraction(
                    pdf_bytes,
                    assessment_id=assessment_id,
                    is_diagnostic=is_diagnostic,
                    wanted=wanted,
                    progress_callback=progress_callback,
                )
                # Use the existing DB-allowed enum value - the canonical
                # path produces structured Q&A pairs equivalent to the
                # legacy pdf_qa_auto mode.
                # (Constraint: creation_mode IN manual|pdf_qa_auto|pdf_ai|mixed)
                creation_mode = "pdf_qa_auto"
                source_type = "pdf_qa_converted"
                warnings.extend(canon_warnings)

                class _ExtractionShim:
                    """Minimal duck-type for the downstream confidence math /
                    optional title lift. The canonical path does not currently
                    lift a title; None keeps ``assessment.title`` unchanged."""

                    title = None
                    confidence = canon_conf

                    def model_dump(self):
                        return {
                            "parser": canonical_parser_name,
                            "warnings": canon_warnings,
                            "confidence": canon_conf,
                        }

                extraction = _ExtractionShim()
            except AssessmentDocValidationError as exc:
                if is_diagnostic:
                    # Diagnostics need the printed numbering and answer key
                    # intact - never fall back to a path that can renumber.
                    raise
                # Quizzes keep the pre-existing behavior: if the paper
                # cannot be parsed as Q&A, build MCQs from the content.
                # Logged + surfaced in warnings so the fallback is visible.
                logger.warning(
                    "Canonical extraction failed for quiz %s, using legacy quiz path: %s",
                    assessment_id,
                    exc.detail or exc,
                )
                warnings.append(
                    f"Layout-aware Q&A extraction skipped: {exc.detail or exc}"
                )
                batch_questions = []
                canonical_source_numbers = []
                pair_answer_texts = []
                extraction = None

        canonical_used = bool(canonical_source_numbers)

        if canonical_used and is_diagnostic:
            # Canonical path already produced the diagnostic questions.
            pass
        elif is_diagnostic:
            # Strict Q&A path for diagnostics (BUG-06 validation).
            _report_progress(progress_callback, "extract", 68, "Extracting Q&A pairs...")
            extraction = extract_qa_from_text(text)
            assert_valid_extraction(extraction)
            pairs = pair_questions_answers(extraction)
            assert_valid_pairs(pairs)
            if wanted is not None and len(pairs) > wanted:
                matched_total = len(pairs)
                pairs = pairs[:wanted]
                warnings.append(
                    f"Using first {wanted} of {matched_total} matched Q&A pairs"
                )
            _report_progress(progress_callback, "convert", 78, "Converting to MCQs...")
            converted = convert_pairs_batch(
                pairs, quiz_title=extraction.title or assessment.title
            )
            assert_valid_mcqs(converted)
            # Flat MCQ papers often lack SECTION headings — label real topics
            # from question content instead of dumping everything into "General".
            if not parse_section_topics(text):
                from app.services.quiz.concept_labeler import assign_learning_concepts

                converted.questions = assign_learning_concepts(list(converted.questions or []))
            assert_topic_or_key_area_available(
                text,
                assessment_type=assessment.assessment_type,
                topic_id=topic_id,
                mcq_questions=converted.questions,
            )
            batch_questions = list(converted.questions or [])
            failed_conversions = list(converted.failed_conversions or [])
            warnings.extend(list(extraction.warnings or []))
            pair_answer_texts = [
                pairs[i].answer_text if i < len(pairs) else None
                for i in range(len(batch_questions))
            ]
        else:
            # Quizzes: prefer Q&A when the PDF looks like an answer-key doc;
            # otherwise skip that LLM round-trip and generate from content.
            if canonical_used:
                # Q&A already extracted from the PDF layout; only the
                # top-up / content-generation steps below still apply.
                pass
            elif _looks_like_qa_document(text):
                _report_progress(progress_callback, "extract", 68, "Checking for Q&A layout...")
                try:
                    extraction = extract_qa_from_text(text)
                    pairs = pair_questions_answers(extraction) or []
                    if pairs:
                        matched_total = len(pairs)
                        if wanted is not None and matched_total > wanted:
                            pairs = pairs[:wanted]
                            warnings.append(
                                f"Using first {wanted} of {matched_total} matched Q&A pairs"
                            )
                        _report_progress(
                            progress_callback, "convert", 76, "Converting Q&A to MCQs..."
                        )
                        converted = convert_pairs_batch(
                            pairs, quiz_title=extraction.title or assessment.title
                        )
                        batch_questions = list(converted.questions or [])
                        failed_conversions = list(converted.failed_conversions or [])
                        warnings.extend(list(extraction.warnings or []))
                        pair_answer_texts = [
                            pairs[i].answer_text if i < len(pairs) else None
                            for i in range(len(batch_questions))
                        ]
                except Exception as qa_exc:  # noqa: BLE001 - fall back to content MCQs
                    logger.info(
                        "Q&A extraction unavailable for assessment %s (%s); using content MCQs",
                        assessment_id,
                        qa_exc,
                    )
                    warnings.append(f"Q&A extraction skipped: {qa_exc}")
                    pairs = []
                    batch_questions = []
                    extraction = None
            else:
                warnings.append(
                    "PDF did not look like a Q&A / answer-key document; "
                    "generating MCQs from lesson content."
                )

            topic_name = (assessment.title or "Quiz").strip() or "Quiz"
            gen_count = wanted if wanted is not None else 10
            if not batch_questions:
                creation_mode = "pdf_ai"
                source_type = "pdf_ai"
                _report_progress(
                    progress_callback,
                    "generate",
                    72,
                    f"Generating {gen_count} MCQs from PDF content...",
                )
                batch_questions = _generate_mcqs_from_any_pdf(text, topic_name, gen_count)
                pair_answer_texts = [None] * len(batch_questions)
                warnings.append(
                    f"Generated {len(batch_questions)} MCQs from PDF content "
                    f"(requested {gen_count})."
                )
            elif wanted is not None and len(batch_questions) < wanted:
                # Top up short Q&A conversions so the teacher's requested count is honored.
                need = wanted - len(batch_questions)
                _report_progress(
                    progress_callback,
                    "generate",
                    82,
                    f"Adding {need} more MCQs to reach {wanted}...",
                )
                exclude = [(q.question_text or "")[:200] for q in batch_questions]
                extra = _generate_mcqs_from_any_pdf(
                    text, topic_name, need, exclude_question_texts=exclude
                )
                if extra:
                    creation_mode = "mixed"
                    source_type = "mixed"
                    batch_questions = list(batch_questions) + list(extra)
                    pair_answer_texts = list(pair_answer_texts) + [None] * len(extra)
                    warnings.append(
                        f"Topped up with {len(extra)} content MCQs "
                        f"(requested {wanted}, now {len(batch_questions)})."
                    )
                if len(batch_questions) < wanted:
                    warnings.append(
                        f"Could only produce {len(batch_questions)} of {wanted} requested MCQs."
                    )

            if not batch_questions:
                raise AssessmentDocValidationError(
                    detail=(
                        "Could not create MCQs from this PDF. "
                        "Try a clearer document or a different file."
                    )
                )

        sections = parse_section_topics(text)
        question_pdf_topics: dict[str, str] = {}
        question_concepts: dict[str, str] = {}

        _report_progress(progress_callback, "save", 88, "Reviewing display format...")
        try:
            from app.services.quiz.display_format_qa import review_mcqs_for_display

            batch_questions = review_mcqs_for_display(list(batch_questions))
        except Exception as exc:
            logger.warning("Display format review before save skipped: %s", exc)

        _report_progress(progress_callback, "save", 90, "Saving questions...")
        db = get_db()
        db.query(AssessmentQuestion).filter(
            AssessmentQuestion.assessment_id == assessment_id
        ).delete()
        db.commit()

        created_ids = []
        confidences = []
        for idx, mcq in enumerate(batch_questions):
            fields = mcq_to_question_fields(mcq)
            raw_ans = pair_answer_texts[idx] if idx < len(pair_answer_texts) else None
            if creation_mode == "pdf_ai":
                q_source = "pdf_ai"
            elif creation_mode == "mixed":
                q_source = "pdf_qa_converted" if raw_ans is not None else "pdf_ai"
            else:
                q_source = "pdf_qa_converted"
            # Prefer the printed number from the canonical parser when it
            # owns this run - that's the whole point of the new
            # architecture (spec Step 5 / Step 19: no ``idx + 1``). Fall
            # back to 1-based index for the legacy path where authoritative
            # source numbering is not available.
            if canonical_used:
                # Printed number for PDF questions; content MCQs topped up
                # after them have no source question, so store None rather
                # than an index that could collide with a printed number.
                q_source_number = (
                    canonical_source_numbers[idx]
                    if idx < len(canonical_source_numbers)
                    else None
                )
            else:
                q_source_number = idx + 1
            q = question_bank_service.create_question(
                created_by=user_id,
                topic_id=topic_id,
                source_type=q_source,
                source_pdf_thread_id=rag_thread_id,
                source_question_number=q_source_number,
                correct_answer_raw=raw_ans,
                **fields,
            )
            created_ids.append(q.id)
            confidences.append(fields.get("extraction_confidence") or 0.85)
            if pairs and idx < len(pairs):
                qnum = question_number_from_label(pairs[idx].question_number)
                section_topic = topic_for_question_number(qnum, sections) if sections else None
                if section_topic:
                    question_pdf_topics[str(q.id)] = section_topic
                    question_concepts[str(q.id)] = section_topic
            concept = getattr(mcq, "learning_concept", None)
            if concept and str(q.id) not in question_concepts:
                question_concepts[str(q.id)] = str(concept).strip()

        assessment_service.add_questions(assessment_id, created_ids)

        if not created_ids:
            raise AssessmentDocValidationError(
                detail="MCQ conversion failed — no questions were saved."
            )

        extract_conf = (
            float(getattr(extraction, "confidence", 0.75) or 0.75) if extraction else 0.75
        )
        overall = (
            sum(confidences) / len(confidences) * 0.5 + extract_conf * 0.5
            if confidences
            else extract_conf
        )

        raw_json = json.dumps(
            {
                "mode": creation_mode,
                "requested_question_count": wanted,
                "extraction": extraction.model_dump() if extraction else None,
                "pair_count": len(pairs),
                "converted_count": len(batch_questions),
                "failed_conversions": failed_conversions,
                "warnings": warnings,
            },
            ensure_ascii=False,
        )

        assessment_service.save_pdf_extraction(
            source.id,
            raw_json=raw_json,
            pair_count=len(pairs) or len(created_ids),
            warnings_json=json.dumps(warnings, ensure_ascii=False) if warnings else None,
            overall_confidence=overall,
        )

        assessment = assessment_service.get_assessment(assessment_id)
        assessment.creation_mode = creation_mode
        # Diagnostics stay draft until admin explicitly publishes/approves.
        # Students only see published diagnostics via get_active_platform_diagnostic.
        if assessment.assessment_type == "diagnostic":
            assessment.status = "draft"
            assessment.requires_review = True
        if extraction and extraction.title and assessment.title.startswith("Untitled"):
            assessment.title = extraction.title
        if question_pdf_topics or question_concepts:
            meta = {}
            if assessment.description:
                try:
                    meta = json.loads(assessment.description)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                if not isinstance(meta, dict):
                    meta = {}
            if question_pdf_topics:
                meta["question_pdf_topics"] = question_pdf_topics
            if question_concepts:
                meta["question_concepts"] = question_concepts
            if assessment.assessment_type == "diagnostic":
                meta["awaiting_admin_approval"] = True
            assessment.description = json.dumps(meta, ensure_ascii=False)
        db.commit()

        if assessment.assessment_type == "diagnostic":
            try:
                from app.services.lms.diagnostic_timer_service import estimate_question_times
                estimate_question_times(assessment_id)
            except Exception as timer_exc:
                logger.warning("Diagnostic timer estimation failed: %s", timer_exc)

        _report_progress(progress_callback, "done", 100, "Quiz ready")
        return {
            "assessment_id": assessment_id,
            "source_id": source.id,
            "thread_id": rag_thread_id,
            "pair_count": len(pairs),
            "question_count": len(created_ids),
            "requested_question_count": wanted,
            "mode": creation_mode,
            "failed_conversions": failed_conversions,
            "overall_confidence": round(overall, 3),
            "requires_review": True if assessment.assessment_type == "diagnostic" else (overall < 0.85),
            "status": assessment.status,
            "extraction_status": "completed",
            "warnings": warnings,
        }
    except AssessmentDocValidationError as exc:
        logger.warning(
            "PDF quiz format validation failed for assessment %s: %s",
            assessment_id,
            exc.detail or str(exc),
        )
        _set_pdf_source_status(source.id, "failed", error_message=public_error_message(exc))
        raise
    except Exception as exc:
        logger.exception("PDF quiz pipeline failed for assessment %s", assessment_id)
        _set_pdf_source_status(source.id, "failed", error_message=str(exc))
        raise
