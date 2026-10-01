"""Parser-agnostic document representation.

Every :class:`DocumentParser` implementation returns a
:class:`CanonicalDocument`. Downstream MCQ parsing must depend only on this
module - never on paddle / docling / marker / pymupdf types directly - so the
parser engine can be swapped without touching MCQ code.

Traceability contract: every ``CanonicalBlock`` carries ``page_number``,
``bbox`` (when available) and ``reading_order`` so a generated MCQ can be
traced back to the exact PDF region it was extracted from. This answers the
"where did Q11 come from?" question the pasted spec (Step 11) requires.
"""

from __future__ import annotations

import enum
from typing import List, Optional, Sequence

from pydantic import BaseModel, Field


class BlockType(str, enum.Enum):
    """Canonical block kinds. Parsers must map their native labels onto these."""

    #: Ordinary running text - stems, options, prose.
    TEXT = "text"
    #: A block-level equation. ``latex`` is authoritative; ``text`` is fallback.
    EQUATION = "equation"
    #: A layout heading (section / question-set title).
    HEADING = "heading"
    #: A table cell / row. Answer keys are typically laid out as tables.
    TABLE = "table"
    #: A list item (numbered / bulleted).
    LIST_ITEM = "list_item"
    #: A page header or footer (usually filtered before MCQ parsing).
    HEADER_FOOTER = "header_footer"
    #: Anything else the parser could not classify.
    UNKNOWN = "unknown"


class BBox(BaseModel):
    """Bounding box in PDF (top-left origin) points. All fields optional so
    parsers that cannot supply geometry still round-trip cleanly."""

    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)


class CanonicalBlock(BaseModel):
    """One structural unit on a page.

    - ``text`` is the human-readable body. For an EQUATION block it may be
      empty when only ``latex`` is meaningful.
    - ``latex`` is the parser's LaTeX reconstruction of any math in the block.
      Downstream code should render this instead of trying to re-derive math
      from ``text`` with regex - that is the exact class of hack the new
      architecture replaces.
    - ``inline_math`` holds LaTeX for math *inside* a text block (subscripts,
      exponents inline with prose), keyed left-to-right in reading order.
    """

    block_id: str
    page_number: int = Field(..., ge=1)
    block_type: BlockType = BlockType.TEXT
    text: str = ""
    latex: Optional[str] = None
    inline_math: List[str] = Field(default_factory=list)
    bbox: Optional[BBox] = None
    reading_order: int = 0
    #: Parser-reported confidence for this block (0-1); None if not supplied.
    confidence: Optional[float] = None
    #: Free-form extra metadata a specific parser wants to preserve.
    #: Downstream MCQ code must not rely on any particular key here.
    extra: dict = Field(default_factory=dict)


class CanonicalPage(BaseModel):
    """One PDF page's canonical blocks in reading order."""

    page_number: int = Field(..., ge=1)
    width: Optional[float] = None
    height: Optional[float] = None
    blocks: List[CanonicalBlock] = Field(default_factory=list)


class CanonicalDocument(BaseModel):
    """The whole PDF as parser-agnostic canonical blocks.

    - ``pages`` are in printed order, each with its blocks in reading order.
    - ``parser`` records which adapter produced this (for telemetry / debug).
    - ``fallback_used`` is True when the primary parser could not handle the
      document and an alternate adapter (or degraded mode) was used.
    """

    document_id: str
    parser: str
    parser_version: str = ""
    pages: List[CanonicalPage] = Field(default_factory=list)
    fallback_used: bool = False
    warnings: List[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)

    def iter_blocks(self) -> Sequence[CanonicalBlock]:
        """All blocks across all pages in reading order."""
        return [b for page in self.pages for b in page.blocks]

    def page_count(self) -> int:
        return len(self.pages)
