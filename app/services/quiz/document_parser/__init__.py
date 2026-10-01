"""Document intelligence layer for the PDF -> MCQ pipeline.

This package is the *only* place the quiz system depends on a specific PDF
extraction engine. Downstream MCQ parsing consumes the parser-agnostic
:class:`CanonicalDocument` produced by any :class:`DocumentParser`
implementation, so a new engine (Docling, Mathpix, ...) can be added by
implementing the interface without touching the MCQ code.

Design principles (see report.txt for full context):

- The PDF is the source of truth for question identity, ordering and math.
- Text is *never* flattened before MCQ boundary detection - blocks carry
  page number, bounding box, reading order and detected type so the MCQ
  parser can reason structurally instead of guessing from a plain string.
- Math structure comes from the parser (font-geometry / equation OCR),
  not from downstream regex.
- LLM assistance is opt-in *after* deterministic parsing, never a silent
  fallback that renumbers the document.
"""

from app.services.quiz.document_parser.canonical import (
    BBox,
    BlockType,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalPage,
)
from app.services.quiz.document_parser.parser import (
    DocumentParser,
    DocumentParserError,
    ParserQualityReport,
    get_document_parser,
)

__all__ = [
    "BBox",
    "BlockType",
    "CanonicalBlock",
    "CanonicalDocument",
    "CanonicalPage",
    "DocumentParser",
    "DocumentParserError",
    "ParserQualityReport",
    "get_document_parser",
]
