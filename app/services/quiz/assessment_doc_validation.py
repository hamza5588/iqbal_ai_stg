"""Validate assessment Q&A documents before / during PDF→MCQ ingest.

BUG-06: invalid or wrong-format uploads must fail with one clear user-facing
message instead of raw pipeline exceptions.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional, Sequence

from app.services.lms.exceptions import LMSValidationError
from app.services.quiz.section_topics import parse_section_topics

ASSESSMENT_FORMAT_ERROR = (
    "This document does not match the required assessment format. "
    "Please upload a valid document."
)

_SUPPORTED_EXTENSIONS = {".pdf"}
_PDF_MAGIC = b"%PDF"


class AssessmentDocValidationError(LMSValidationError):
    """Format / structure failure for an uploaded assessment document.

    Structure failures keep the canonical ASSESSMENT_FORMAT_ERROR text. File-level
    preflight failures (scanned, corrupted, password-protected, no pages) carry a
    specific `public_message` and `reason` so the teacher knows what to fix.
    """

    def __init__(
        self,
        detail: Optional[str] = None,
        *,
        public_message: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        self.detail = (detail or "").strip() or None
        self.public_message = (public_message or "").strip() or ASSESSMENT_FORMAT_ERROR
        self.reason = reason or "INVALID_FORMAT"
        super().__init__(self.public_message)


def _raise(detail: str) -> None:
    raise AssessmentDocValidationError(detail=detail)


def assert_supported_assessment_file(
    filename: Optional[str],
    file_bytes: Optional[bytes],
    *,
    label: str = "Assessment document",
) -> None:
    """Supported format + empty-document checks (run before ingest)."""
    data = file_bytes or b""
    if not data:
        _raise(f"{label} is empty")

    name = (filename or "").strip()
    ext = os.path.splitext(name)[1].lower() if name else ""
    if ext and ext not in _SUPPORTED_EXTENSIONS:
        _raise(f"{label} must be a PDF file")

    # Reject obvious non-PDFs even when the extension is missing or wrong.
    if not data.lstrip().startswith(_PDF_MAGIC):
        # Allow leading whitespace / BOM-less files that still start with %PDF
        # after a small BOM; otherwise treat as wrong format.
        head = data[:1024].lstrip()
        if not head.startswith(_PDF_MAGIC):
            _raise(f"{label} is not a valid PDF")

    # Open the file before any LLM / vision / ingest work so scanned, corrupted,
    # password-protected and empty PDFs are rejected in seconds, not minutes.
    from app.utils.pdf_preflight import preflight_check_pdf

    # Assessment pipelines (LlamaParse / page-image vision) can read mostly-scanned pages,
    # so only PDFs with no text layer at all are rejected as scanned here.
    preflight = preflight_check_pdf(data, reject_mostly_scanned=False)
    if not preflight.get("ok"):
        message = preflight.get("message") or ASSESSMENT_FORMAT_ERROR
        # Diagnostics upload several PDFs at once: say which one failed.
        if label and label != "Assessment PDF":
            message = f"{label}: {message}"
        raise AssessmentDocValidationError(
            detail=f"{label}: {preflight.get('code')}",
            public_message=message,
            reason=preflight.get("code"),
        )


def assert_nonempty_extracted_text(text: Optional[str]) -> None:
    if not (text or "").strip():
        _raise("Document has no readable text (empty or unscannable)")


def assert_valid_extraction(extraction) -> None:
    """Require at least one real question and an answer key or native MCQ options."""
    questions = list(getattr(extraction, "questions", None) or [])
    answers = list(getattr(extraction, "answers", None) or [])

    valid_questions = [q for q in questions if (getattr(q, "text", None) or "").strip()]
    if not valid_questions:
        _raise("No valid questions found in document")

    has_answers = any((getattr(a, "text", None) or "").strip() for a in answers)
    has_options = any(
        getattr(q, "options", None) and len(q.options) >= 2 for q in valid_questions
    )
    if not has_answers and not has_options:
        _raise("No answer key or multiple-choice options found in document")


def assert_valid_pairs(pairs: Sequence) -> None:
    """Require at least one matched question + answer with content."""
    if not pairs:
        _raise("Could not match questions with answers")

    valid = [
        p
        for p in pairs
        if (getattr(p, "question_text", None) or "").strip()
        and (getattr(p, "answer_text", None) or "").strip()
    ]
    if not valid:
        _raise("Matched pairs are missing question or answer text")


def assert_valid_mcqs(batch) -> None:
    """Require ≥1 converted MCQ with options and a correct answer."""
    questions = list(getattr(batch, "questions", None) or [])
    if not questions:
        detail = "No valid multiple-choice questions could be built from the document"
        failed = list(getattr(batch, "failed_conversions", None) or [])
        if failed:
            detail += ": " + "; ".join(str(x) for x in failed[:3])
        _raise(detail)

    for mcq in questions:
        options = list(getattr(mcq, "options", None) or [])
        if len(options) < 2:
            _raise("A question is missing valid answer options")
        correct = getattr(mcq, "correct_option_label", None)
        labels = {getattr(o, "label", None) for o in options}
        if not correct or correct not in labels:
            _raise("A question is missing a valid correct answer")


def assert_topic_or_key_area_available(
    text: Optional[str],
    *,
    assessment_type: Optional[str] = None,
    topic_id: Optional[int] = None,
    mcq_questions: Optional[Iterable] = None,
) -> None:
    """Require topic / key-area signal for diagnostics (SECTION/PART or concepts)."""
    if topic_id:
        return
    if parse_section_topics(text or ""):
        return
    for mcq in mcq_questions or []:
        if (getattr(mcq, "learning_concept", None) or "").strip():
            return
    # Quizzes can be published without section headings; diagnostics must map
    # questions to key areas for Learning Chat / mastery.
    if (assessment_type or "").strip().lower() == "diagnostic":
        _raise("No topic or key-area headings found in document")


def public_error_message(exc: BaseException) -> str:
    """Always surface the canonical format message for format failures."""
    if isinstance(exc, AssessmentDocValidationError):
        return exc.public_message
    return str(exc) or ASSESSMENT_FORMAT_ERROR
