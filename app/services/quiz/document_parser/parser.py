"""Document parser interface + registry.

The quiz pipeline resolves a parser by NAME (from configuration), never by
importing an implementation directly. This lets deployment choose the
engine that matches its environment (native / paddle / marker / mathpix)
without a code change.

Selection order (first match wins):
    1. ``QUIZ_DOCUMENT_PARSER`` environment variable.
    2. Flask app config ``QUIZ_DOCUMENT_PARSER`` (if a Flask app context exists).
    3. The default fallback ``"native"`` (PyMuPDF-based, always available).

An implementation MUST:
    - accept raw PDF bytes,
    - be re-entrant (Celery workers reuse instances),
    - never mutate the input,
    - raise :class:`DocumentParserError` on failure (never return an empty
      :class:`CanonicalDocument` silently - that hid the current bug).
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional

from pydantic import BaseModel

from app.services.quiz.document_parser.canonical import CanonicalDocument

logger = logging.getLogger(__name__)


class DocumentParserError(RuntimeError):
    """Raised when a parser cannot produce a usable :class:`CanonicalDocument`.

    The caller is expected to log it, mark the extraction as failed, and
    surface a structured error to the user - never silently swap in an
    LLM-only guess.
    """


class ParserQualityReport(BaseModel):
    """Cheap gate used by the pipeline BEFORE running the deterministic MCQ
    parser: is this document good enough to trust, or should we fall back?

    A parser's :meth:`DocumentParser.assess_quality` should be fast (avoid
    running the whole model stack) - it exists so the pipeline can pick a
    cheaper engine on a healthy digital PDF and a costlier one on a scan.
    """

    #: True when the parser is confident it can extract structure from this
    #: PDF (native text layer present, pages parseable).
    acceptable: bool
    #: Free-form reason, surfaced in logs / telemetry.
    reason: str = ""
    #: Overall 0-1 quality score when the parser can supply one.
    score: Optional[float] = None
    #: Number of pages successfully sampled during the quality check.
    sampled_pages: int = 0


class DocumentParser(ABC):
    """Adapter interface. All engines implement this contract."""

    #: Short slug used in configuration and telemetry, e.g. ``"paddle"``.
    name: str = "base"
    #: Human-readable version (e.g. underlying library / model version).
    version: str = ""

    @abstractmethod
    def parse(self, pdf_bytes: bytes, *, document_id: str) -> CanonicalDocument:
        """Return a canonical representation of the PDF."""

    def assess_quality(self, pdf_bytes: bytes) -> ParserQualityReport:  # noqa: D401
        """Cheap pre-check. Default is 'assume acceptable'; override in adapters
        that can inspect the PDF quickly (e.g. detect a scan-only document)."""
        return ParserQualityReport(acceptable=True, reason="default", sampled_pages=0)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ParserFactory = Callable[[], DocumentParser]
_REGISTRY: Dict[str, _ParserFactory] = {}
_INSTANCES: Dict[str, DocumentParser] = {}


def register_parser(name: str, factory: _ParserFactory) -> None:
    """Register a parser factory under a slug.

    Factories are lazy: they only build the underlying model when the parser
    is first requested, so importing this package never pays for the model
    load. That matters for Celery workers that don't need the parser.
    """
    key = name.strip().lower()
    if not key:
        raise ValueError("Parser name must be a non-empty string")
    _REGISTRY[key] = factory


def _resolve_configured_name() -> str:
    env_name = (os.environ.get("QUIZ_DOCUMENT_PARSER") or "").strip().lower()
    if env_name:
        return env_name
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            cfg = current_app.config.get("QUIZ_DOCUMENT_PARSER")
            if cfg:
                return str(cfg).strip().lower()
    except Exception:  # noqa: BLE001 - Flask absent / no app context
        pass
    return "native"


def get_document_parser(name: Optional[str] = None) -> DocumentParser:
    """Return the (cached) parser matching ``name`` or the configured default.

    Raises :class:`DocumentParserError` if the requested parser is not
    registered - callers should not fall back to some other engine silently.
    """
    key = (name or _resolve_configured_name()).strip().lower()
    if key in _INSTANCES:
        return _INSTANCES[key]
    factory = _REGISTRY.get(key)
    if not factory:
        available = sorted(_REGISTRY.keys()) or ["<none registered>"]
        raise DocumentParserError(
            f"Document parser {key!r} is not registered. Available: {available}"
        )
    try:
        instance = factory()
    except Exception as exc:  # noqa: BLE001 - re-raise as parser error
        logger.exception("Document parser %r failed to initialize", key)
        raise DocumentParserError(
            f"Parser {key!r} failed to initialize: {exc}"
        ) from exc
    _INSTANCES[key] = instance
    return instance


# ---------------------------------------------------------------------------
# Built-in registrations
#
# Adapters register themselves here so a caller only needs
# ``get_document_parser()`` - and so an environment that cannot import a
# heavyweight dependency (paddleocr on a slim worker) still gets a working
# default.
# ---------------------------------------------------------------------------

def _register_builtin_parsers() -> None:
    # Native PyMuPDF adapter - always available (pymupdf is already in
    # requirements.txt), no model download, safe default.
    def _native_factory() -> DocumentParser:
        from app.services.quiz.document_parser.native_parser import (
            NativePyMuPDFParser,
        )
        return NativePyMuPDFParser()

    register_parser("native", _native_factory)

    # Paddle (PP-StructureV3 + PP-FormulaNet) - only registered when the
    # library is importable, so a deployment without paddle does not fail
    # at startup.
    def _paddle_factory() -> DocumentParser:
        from app.services.quiz.document_parser.paddle_parser import (
            PaddleDocumentParser,
        )
        return PaddleDocumentParser()

    register_parser("paddle", _paddle_factory)


_register_builtin_parsers()
