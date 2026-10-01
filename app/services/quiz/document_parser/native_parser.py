"""Native PyMuPDF-based DocumentParser.

Layout-aware extraction using the ``pymupdf`` library that already ships in
``requirements.txt``. This adapter is the safe default: it needs no model
download, runs synchronously in-process and works on every Python version
this project supports.

Key property: math structure is recovered from *font geometry*, not from
regex on flattened text. PyMuPDF exposes each span's ``size``, ``origin``
and ``bbox``, so a digit rendered as a superscript (smaller font, higher
baseline) can be reconstructed as ``^{...}`` from evidence rather than
guessed after the fact. That is the mechanism that replaces the old
``recover_spaced_power`` / ``recover_scientific_notation`` /
``recover_log_subscripts`` heuristics.

Limitations (documented, not hidden):
    - Scanned/image-only PDFs return empty pages here. Use the paddle
      adapter for those; the pipeline decides via ``assess_quality``.
    - Complex layouts (multi-column with math flowing across columns) are
      handled at "good enough for MCQ papers" quality - the deterministic
      MCQ parser is tolerant of missing bboxes.
"""

from __future__ import annotations

import io
import logging
import re
from typing import List, Optional

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
)

logger = logging.getLogger(__name__)


# A span is treated as a superscript when its font is meaningfully smaller
# than the line's dominant font AND its baseline sits above that dominant
# baseline. These thresholds are conservative - false positives here corrupt
# the printed text - and were chosen against the DIL SAATHI PDF's spans.
_SUPER_SIZE_RATIO = 0.85
_SUPER_RISE_RATIO = 0.20  # baseline lifted at least 20% of line height
_SUB_DROP_RATIO = 0.15  # baseline dropped at least 15% of line height

_METADATA_LINE_RE = re.compile(
    r"^\s*(?:domain|topic|cognitive\s+level|difficulty|marks?|coverage)\s*[:\-]",
    re.I,
)


def _lazy_import_fitz():
    """Import pymupdf lazily so importing the package never requires it."""
    try:
        import pymupdf as fitz  # noqa: WPS433

        return fitz
    except ImportError:  # pragma: no cover - old install fallback
        try:
            import fitz  # type: ignore[no-redef]  # noqa: WPS433

            return fitz
        except ImportError as exc:
            raise DocumentParserError(
                "PyMuPDF (pymupdf) is required for the native document parser "
                "but is not installed."
            ) from exc


class NativePyMuPDFParser(DocumentParser):
    """DocumentParser backed by PyMuPDF's ``get_text('dict')`` output."""

    name = "native"

    def __init__(self) -> None:
        # Import here so unit tests that mock DocumentParser do not pull in
        # pymupdf, and so a missing pymupdf raises a clear error at parse()
        # time rather than at import time.
        fitz = _lazy_import_fitz()
        self._fitz = fitz
        self.version = getattr(fitz, "__version__", "unknown")

    # ------------------------------------------------------------------
    # Quality gate
    # ------------------------------------------------------------------
    def assess_quality(self, pdf_bytes: bytes) -> ParserQualityReport:
        """Cheap gate: sample up to the first 3 pages, decide if there is
        enough native text to trust the layer.

        A scanned PDF wrapped as PDF still opens successfully - only the text
        layer is empty. A digital PDF with a corrupted layer has recognizable
        text but often riddled with substitution glyphs (�). Both cases
        should route to a scan-friendly adapter, not this one."""
        try:
            with self._fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                total_pages = doc.page_count
                sample = min(3, total_pages)
                text_len = 0
                bad = 0
                for i in range(sample):
                    txt = doc[i].get_text("text") or ""
                    text_len += len(txt.strip())
                    bad += txt.count("�")
        except Exception as exc:  # noqa: BLE001
            return ParserQualityReport(
                acceptable=False, reason=f"open failed: {exc}", sampled_pages=0
            )
        if text_len < 40:
            return ParserQualityReport(
                acceptable=False,
                reason="text layer empty/near-empty (likely scanned)",
                sampled_pages=sample,
                score=0.0,
            )
        if bad and bad > text_len * 0.05:
            return ParserQualityReport(
                acceptable=False,
                reason=f"substitution-glyph rate {bad}/{text_len} too high",
                sampled_pages=sample,
                score=0.2,
            )
        return ParserQualityReport(
            acceptable=True,
            reason="native text layer present",
            sampled_pages=sample,
            score=0.9,
        )

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------
    def parse(self, pdf_bytes: bytes, *, document_id: str) -> CanonicalDocument:
        try:
            doc = self._fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise DocumentParserError(f"pymupdf could not open PDF: {exc}") from exc

        pages: List[CanonicalPage] = []
        try:
            for page_index in range(doc.page_count):
                page = doc[page_index]
                pages.append(
                    self._parse_page(
                        page, page_number=page_index + 1
                    )
                )
        finally:
            doc.close()

        return CanonicalDocument(
            document_id=document_id,
            parser=self.name,
            parser_version=self.version,
            pages=pages,
        )

    # ------------------------------------------------------------------
    # Page-level extraction
    # ------------------------------------------------------------------
    def _parse_page(self, page, *, page_number: int) -> CanonicalPage:
        width = float(getattr(page.rect, "width", 0.0) or 0.0)
        height = float(getattr(page.rect, "height", 0.0) or 0.0)

        raw = page.get_text("dict") or {}
        raw_blocks = raw.get("blocks") or []
        # PyMuPDF returns blocks in a reasonable reading order for
        # single-column pages already. For multi-column we would need a
        # column-sorting pass - out of scope for this iteration; the
        # deterministic MCQ parser is robust to minor mis-orderings.
        canonical_blocks: List[CanonicalBlock] = []
        for order, raw_block in enumerate(raw_blocks):
            if raw_block.get("type") != 0:  # 0 = text, 1 = image
                continue
            block_bbox = _bbox_from_raw(raw_block.get("bbox"))
            for line_idx, line in enumerate(raw_block.get("lines") or []):
                text, latex = self._render_line(line)
                if not text and not latex:
                    continue
                block_type = BlockType.TEXT
                # A whole line that is exactly one large \frac / long
                # exponent expression is closer to an equation block.
                if latex and not text.strip():
                    block_type = BlockType.EQUATION
                canonical_blocks.append(
                    CanonicalBlock(
                        block_id=f"p{page_number}-b{order}-l{line_idx}",
                        page_number=page_number,
                        block_type=block_type,
                        text=text.strip(),
                        latex=latex,
                        bbox=_bbox_from_raw(line.get("bbox")) or block_bbox,
                        reading_order=len(canonical_blocks),
                    )
                )

        # Metadata banners ("Domain: ... | Topic: ... | ...") are a common
        # noise category in exam PDFs. Tag them so the MCQ parser can drop
        # them from stem/option assembly without a separate regex there.
        for blk in canonical_blocks:
            if _METADATA_LINE_RE.match(blk.text or ""):
                blk.extra.setdefault("is_metadata_banner", True)

        return CanonicalPage(
            page_number=page_number,
            width=width or None,
            height=height or None,
            blocks=canonical_blocks,
        )

    # ------------------------------------------------------------------
    # Superscript / subscript recovery from font geometry
    # ------------------------------------------------------------------
    def _render_line(self, line: dict) -> tuple[str, Optional[str]]:
        """Reconstruct a text line with ``^{...}`` / ``_{...}`` markers on
        spans that PDF rendered as raised/lowered glyphs.

        Approach: compute the line's dominant font size (mode over spans
        weighted by character count) and its dominant baseline y-origin,
        then classify each span:

            small + high baseline -> superscript
            small + low baseline  -> subscript
            otherwise             -> baseline text

        This is what PyMuPDF was designed to give us, and it is why the
        pipeline no longer needs "4 10 -> 4^{10}" regex heuristics: the
        superscript is detected by geometry, not by spelling."""
        spans = line.get("spans") or []
        if not spans:
            return "", None

        # Dominant size = size that carries the most characters.
        size_weight: dict[float, int] = {}
        origin_weight: dict[float, int] = {}
        for span in spans:
            size = float(span.get("size", 0) or 0)
            origin_y = float((span.get("origin") or [0.0, 0.0])[1])
            weight = len(span.get("text") or "")
            if size > 0:
                size_weight[round(size, 2)] = size_weight.get(round(size, 2), 0) + weight
            origin_weight[round(origin_y, 1)] = origin_weight.get(round(origin_y, 1), 0) + weight

        if not size_weight:
            return "".join((s.get("text") or "") for s in spans), None
        base_size = max(size_weight.items(), key=lambda kv: kv[1])[0]
        base_origin = (
            max(origin_weight.items(), key=lambda kv: kv[1])[0]
            if origin_weight
            else 0.0
        )
        line_height = _line_height(line) or base_size or 1.0

        rendered: List[str] = []
        contained_super_or_sub = False
        for span in spans:
            text = span.get("text") or ""
            if not text:
                continue
            size = float(span.get("size", base_size) or base_size)
            origin_y = float((span.get("origin") or [0.0, base_origin])[1])
            role = _classify_span(
                size=size,
                base_size=base_size,
                origin_y=origin_y,
                base_origin=base_origin,
                line_height=line_height,
            )
            if role == "super":
                rendered.append("^{" + text + "}")
                contained_super_or_sub = True
            elif role == "sub":
                rendered.append("_{" + text + "}")
                contained_super_or_sub = True
            else:
                rendered.append(text)

        text_out = "".join(rendered)
        # Normalize whitespace: PyMuPDF sometimes inserts a space between the
        # baseline character and its superscript span - kill that inside
        # ``^{}`` / ``_{}`` groups so ``4 ^{10}`` becomes ``4^{10}``.
        text_out = re.sub(r"\s+(\^\{|_\{)", r"\1", text_out)
        text_out = re.sub(r"[ \t]{2,}", " ", text_out)

        # If we rebuilt any exponent/subscript, expose the same body as
        # ``latex`` so the display layer can typeset it with MathJax without
        # re-guessing structure downstream.
        latex_out = text_out if contained_super_or_sub else None
        return text_out, latex_out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bbox_from_raw(raw) -> Optional[BBox]:
    if not raw or len(raw) < 4:
        return None
    try:
        return BBox(x0=float(raw[0]), y0=float(raw[1]), x1=float(raw[2]), y1=float(raw[3]))
    except (TypeError, ValueError):
        return None


def _line_height(line: dict) -> float:
    bbox = line.get("bbox") or []
    if len(bbox) >= 4:
        try:
            return max(0.0, float(bbox[3]) - float(bbox[1]))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _classify_span(
    *,
    size: float,
    base_size: float,
    origin_y: float,
    base_origin: float,
    line_height: float,
) -> str:
    """Return one of ``"super"``, ``"sub"``, ``"base"`` for a single span."""
    if base_size <= 0 or line_height <= 0:
        return "base"
    if size >= base_size * _SUPER_SIZE_RATIO:
        # Same-sized span cannot be a super/subscript regardless of baseline.
        return "base"
    rise = base_origin - origin_y  # positive = baseline lifted (super)
    if rise >= line_height * _SUPER_RISE_RATIO:
        return "super"
    drop = origin_y - base_origin  # positive = baseline dropped (sub)
    if drop >= line_height * _SUB_DROP_RATIO:
        return "sub"
    return "base"
