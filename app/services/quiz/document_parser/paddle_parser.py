"""PaddleOCR / PP-StructureV3 + PP-FormulaNet adapter.

Runs entirely on CPU (see report.txt / user decision). paddlepaddle 3.3.1
provides cp313 wheels, so this adapter is deployable on the current
Python 3.13 + Windows/Linux Docker stack.

Design notes:
    - The pipeline object is HEAVY (layout + detection + recognition +
      formula recognition models, ~2-3 GB RAM). Only ONE process per host
      should hold it - a Celery worker, not each Gunicorn worker. The
      pipeline is built lazily on first ``parse()`` call and cached on the
      instance so subsequent calls in the same worker reuse it.
    - Layouts are rasterized page-by-page. This is CPU-bound and slow
      (~seconds per page). The pipeline caller must run this in a Celery
      task, never in an HTTP request handler.
    - When ``paddleocr`` is not importable this module still loads (it
      only imports at ``__init__`` time), so an environment without paddle
      installed can still list the parser in the registry but will report
      an unambiguous error on ``get_document_parser("paddle")``.

Configuration (env, all optional):
    QUIZ_PADDLE_DEVICE   - "cpu" (default) or "gpu"
    QUIZ_PADDLE_LANG     - default "en"
    QUIZ_PADDLE_DPI      - rasterization DPI, default 200
"""

from __future__ import annotations

import io
import logging
import os
import uuid
from typing import Any, List, Optional

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


# Paddle block types (PP-StructureV3) mapped onto our canonical enum. Any
# type not in this table becomes BlockType.UNKNOWN so a schema change in
# paddle does not silently degrade extraction quality.
_PADDLE_TYPE_MAP = {
    "text": BlockType.TEXT,
    "paragraph_title": BlockType.HEADING,
    "doc_title": BlockType.HEADING,
    "table": BlockType.TABLE,
    "formula": BlockType.EQUATION,
    "isolate_formula": BlockType.EQUATION,
    "figure": BlockType.UNKNOWN,
    "figure_title": BlockType.HEADING,
    "header": BlockType.HEADER_FOOTER,
    "footer": BlockType.HEADER_FOOTER,
    "page_number": BlockType.HEADER_FOOTER,
    "reference": BlockType.TEXT,
    "algorithm": BlockType.TEXT,
    "seal": BlockType.UNKNOWN,
}


class PaddleDocumentParser(DocumentParser):
    """Adapter around PaddleOCR's structured-document pipeline.

    This class is instantiated lazily by
    :func:`app.services.quiz.document_parser.get_document_parser` and holds
    the pipeline instance for the lifetime of the process. Do not construct
    it in a request handler."""

    name = "paddle"

    def __init__(self) -> None:
        # Fail loudly at construction if the environment cannot import
        # paddleocr - the registry catches this and reports a clean error
        # to the caller. Never silently downgrade to another parser here.
        try:
            import paddleocr  # noqa: F401 - probe availability
        except ImportError as exc:
            raise DocumentParserError(
                "paddleocr is not installed in this environment. Install "
                "'paddlepaddle' + 'paddleocr' or set QUIZ_DOCUMENT_PARSER=native."
            ) from exc

        self._pipeline: Optional[Any] = None
        self._device = (os.environ.get("QUIZ_PADDLE_DEVICE") or "cpu").strip().lower()
        self._lang = (os.environ.get("QUIZ_PADDLE_LANG") or "en").strip().lower()
        try:
            self._dpi = max(72, int(os.environ.get("QUIZ_PADDLE_DPI") or "200"))
        except ValueError:
            self._dpi = 200

        try:
            import paddleocr as _po

            self.version = f"paddleocr {getattr(_po, '__version__', 'unknown')}"
        except Exception:  # noqa: BLE001
            self.version = "paddleocr unknown"

    # ------------------------------------------------------------------
    # Lazy pipeline construction
    # ------------------------------------------------------------------
    def _get_pipeline(self):
        """Build the PP-Structure pipeline once, on first use.

        PaddleOCR 3.x exposes ``PPStructureV3`` (preferred). Older 3.x
        versions expose ``PPStructure``. Use the first available class."""
        if self._pipeline is not None:
            return self._pipeline

        try:
            import paddleocr
        except ImportError as exc:  # pragma: no cover - checked in __init__
            raise DocumentParserError("paddleocr is not installed") from exc

        pipeline_cls = (
            getattr(paddleocr, "PPStructureV3", None)
            or getattr(paddleocr, "PPStructure", None)
        )
        if pipeline_cls is None:
            raise DocumentParserError(
                "paddleocr does not expose PPStructureV3 / PPStructure - "
                "install paddleocr>=3.0"
            )

        kwargs: dict[str, Any] = {}
        # PP-StructureV3 uses ``device`` and ``lang``; older builds accept
        # a subset. Filter unknown kwargs to keep this cross-version safe.
        for key, value in (
            ("device", self._device),
            ("lang", self._lang),
            ("use_doc_orientation_classify", False),
            ("use_doc_unwarping", False),
        ):
            try:
                self._pipeline = pipeline_cls(**{**kwargs, key: value})  # type: ignore[arg-type]
                kwargs[key] = value
            except TypeError:
                # Not accepted by this version - drop it and continue.
                continue

        if self._pipeline is None:
            # No kwargs accepted - construct with defaults.
            self._pipeline = pipeline_cls()
        logger.info(
            "PaddleDocumentParser initialized (device=%s, lang=%s, dpi=%s)",
            self._device,
            self._lang,
            self._dpi,
        )
        return self._pipeline

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def parse(self, pdf_bytes: bytes, *, document_id: str) -> CanonicalDocument:
        images = self._rasterize(pdf_bytes)
        if not images:
            raise DocumentParserError("Could not rasterize any PDF page")

        pipeline = self._get_pipeline()
        pages: List[CanonicalPage] = []
        for idx, image in enumerate(images):
            page_number = idx + 1
            try:
                raw_result = pipeline.predict(input=image)  # PPStructureV3 API
            except AttributeError:
                # Fallback for older PPStructure classes with __call__.
                raw_result = pipeline(image)
            pages.append(self._page_from_paddle_result(raw_result, page_number))

        return CanonicalDocument(
            document_id=document_id or uuid.uuid4().hex,
            parser=self.name,
            parser_version=self.version,
            pages=pages,
        )

    # ------------------------------------------------------------------
    # Rasterization (PyMuPDF - already installed, no new dep)
    # ------------------------------------------------------------------
    def _rasterize(self, pdf_bytes: bytes) -> List["Any"]:
        try:
            import pymupdf as fitz  # noqa: WPS433
        except ImportError:  # pragma: no cover
            import fitz  # type: ignore[no-redef]
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover - required by paddleocr anyway
            raise DocumentParserError("numpy is required for the paddle adapter") from exc

        images: List[Any] = []
        zoom = self._dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise DocumentParserError(f"pymupdf could not open PDF: {exc}") from exc
        try:
            for page in doc:
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                if pix.n == 4:  # RGBA -> RGB
                    arr = arr[:, :, :3]
                # PaddleOCR expects BGR by convention (cv2 style).
                images.append(arr[:, :, ::-1].copy())
        finally:
            doc.close()
        return images

    # ------------------------------------------------------------------
    # Paddle -> CanonicalPage
    # ------------------------------------------------------------------
    def _page_from_paddle_result(self, raw_result: Any, page_number: int) -> CanonicalPage:
        """PPStructureV3.predict() returns either a list of dicts or a
        single result object with ``.parsing_res_list`` depending on the
        library version. Handle both."""
        items = _normalize_paddle_result(raw_result)
        blocks: List[CanonicalBlock] = []
        for order, item in enumerate(items):
            block_type_raw = str(item.get("type") or item.get("block_type") or "text").lower()
            block_type = _PADDLE_TYPE_MAP.get(block_type_raw, BlockType.UNKNOWN)
            bbox = _bbox_from_paddle(item)
            text = _text_from_paddle(item)
            latex = _latex_from_paddle(item, block_type)
            if not text and not latex:
                continue
            blocks.append(
                CanonicalBlock(
                    block_id=f"p{page_number}-b{order}",
                    page_number=page_number,
                    block_type=block_type,
                    text=text,
                    latex=latex,
                    bbox=bbox,
                    reading_order=order,
                    extra={"paddle_type": block_type_raw},
                )
            )
        return CanonicalPage(page_number=page_number, blocks=blocks)

    # ------------------------------------------------------------------
    # Quality gate (cheap: reuse the native parser's text-layer heuristic)
    # ------------------------------------------------------------------
    def assess_quality(self, pdf_bytes: bytes) -> ParserQualityReport:
        # Paddle handles both digital and scanned PDFs, so it is always
        # "acceptable". The pipeline may still PREFER the native parser
        # on healthy digital PDFs for speed - that decision belongs in
        # the pipeline, not here.
        return ParserQualityReport(
            acceptable=True,
            reason="paddle handles digital and scanned PDFs",
            score=0.85,
        )


# ---------------------------------------------------------------------------
# Paddle result-shape helpers (isolated so a future paddle version change
# only touches this section)
# ---------------------------------------------------------------------------

def _normalize_paddle_result(raw: Any) -> List[dict]:
    """Flatten paddle's result variants to a list of dicts."""
    if raw is None:
        return []
    # PPStructureV3.predict() returns a StructureResult with attributes.
    for attr in ("parsing_res_list", "layout_parsing_result", "res"):
        value = getattr(raw, attr, None)
        if value:
            raw = value
            break
    if isinstance(raw, dict):
        # Sometimes the dict already contains a 'parsing_res_list'.
        inner = raw.get("parsing_res_list") or raw.get("res") or raw.get("blocks")
        if inner:
            raw = inner
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[dict] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
        elif hasattr(item, "__dict__"):
            out.append({k: getattr(item, k) for k in item.__dict__ if not k.startswith("_")})
    return out


def _bbox_from_paddle(item: dict) -> Optional[BBox]:
    for key in ("bbox", "block_bbox", "box", "coords"):
        raw = item.get(key)
        if raw and len(raw) >= 4:
            try:
                return BBox(
                    x0=float(raw[0]), y0=float(raw[1]),
                    x1=float(raw[2]), y1=float(raw[3]),
                )
            except (TypeError, ValueError):
                continue
    return None


def _text_from_paddle(item: dict) -> str:
    # PPStructureV3 uses ``block_content`` / ``text``; the OCR sub-result
    # may nest text under ``res -> text``.
    for key in ("block_content", "text", "content"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    res = item.get("res")
    if isinstance(res, dict):
        for key in ("text", "html", "content"):
            value = res.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _latex_from_paddle(item: dict, block_type: BlockType) -> Optional[str]:
    if block_type != BlockType.EQUATION:
        return None
    for key in ("latex", "block_content", "rec_res", "content"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    res = item.get("res")
    if isinstance(res, dict):
        for key in ("latex", "text"):
            value = res.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None
