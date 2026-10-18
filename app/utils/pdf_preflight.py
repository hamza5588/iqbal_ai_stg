"""
Fast, synchronous PDF preflight shared by every teacher PDF upload (lesson,
quiz, diagnostic). It runs before any expensive work (LlamaParse, vision LLM,
embeddings, background jobs) so an unusable file is rejected in seconds with
a specific reason instead of failing minutes later.

Rejects: decompression bombs, corrupted/unopenable files, password-protected
files, PDFs with no pages, and scanned/image-only PDFs without a usable text
layer. A text-based PDF that also contains images or diagrams is accepted.
None of these upload flows has OCR that runs before the text-layer check in
`ingest_pdf`, so a scanned file could never be processed anyway.
"""
from __future__ import annotations

import re
import threading
import zlib

# Per-code user-facing messages. Keep them short, specific and actionable.
PDF_UNREADABLE_MESSAGE = (
    "This file could not be opened as a PDF. It may be corrupted or not actually "
    "a PDF file. Please check the file and upload it again."
)
PDF_PASSWORD_PROTECTED_MESSAGE = (
    "This PDF is password-protected. Please remove the password and upload it again."
)
PDF_EMPTY_MESSAGE = "This PDF has no pages. Please upload a PDF with content."
PDF_SCANNED_MESSAGE = (
    "This PDF contains scanned images without readable text. "
    "Please upload a supported text-based PDF."
)
PDF_NO_TEXT_MESSAGE = (
    "This PDF has no readable text. Please upload a supported text-based PDF "
    "(text you can select and copy)."
)
PDF_TIMEOUT_MESSAGE = (
    "This PDF took too long to validate and may be malformed or unusually complex. "
    "Please try a different file."
)

# A page "has usable text" when it carries at least this many non-whitespace
# characters. Scanned books often keep a stray page number or running header
# in the text layer, which must not count as real content.
MIN_PAGE_TEXT_CHARS = 40
# Image-heavy documents where fewer than this share of scanned pages have
# usable text are treated as scanned.
MIN_TEXT_PAGE_RATIO = 0.10

_STREAM_START_RE = re.compile(rb'stream\r?\n')
_MAX_BOMB_SCAN_STREAMS = 200


def check_decompression_bomb(file_bytes: bytes, max_output_mb: int = 60) -> str:
    """
    Cheap pre-scan for PDF decompression bombs: a FlateDecode stream that
    expands far beyond anything a legitimate single PDF stream (a page's
    content, one embedded font, one image) should ever need. Runs on the
    raw bytes before fitz/pypdf ever open the file, so a bomb is rejected
    before paying the cost of fully decompressing it.

    Scoped to FlateDecode, the overwhelmingly common PDF compression
    filter — other filters still fall back on the existing upload-size cap.
    Returns an error message string, or '' if clean.
    """
    max_output_bytes = max_output_mb * 1024 * 1024
    scanned = 0
    for match in _STREAM_START_RE.finditer(file_bytes):
        if scanned >= _MAX_BOMB_SCAN_STREAMS:
            break
        start = match.end()
        end = file_bytes.find(b'endstream', start)
        if end == -1:
            continue
        raw = file_bytes[start:end]
        scanned += 1
        if len(raw) < 256:
            continue  # too small to be a bomb
        try:
            decompressor = zlib.decompressobj()
            out_len = 0
            out = decompressor.decompress(raw, max_output_bytes + 1)
            out_len += len(out)
            while decompressor.unconsumed_tail and out_len <= max_output_bytes:
                out = decompressor.decompress(decompressor.unconsumed_tail, max_output_bytes + 1 - out_len)
                if not out:
                    break
                out_len += len(out)
            if out_len > max_output_bytes:
                return (
                    f"This PDF contains a compressed stream that expands to over {max_output_mb} MB "
                    "when decompressed, which is far larger than any normal document needs. It was "
                    "rejected to protect server memory — please upload a different file."
                )
        except zlib.error:
            continue  # not a zlib stream at this offset — not what we're checking for
    return ''


def preflight_check_pdf_inner(
    file_bytes: bytes, max_scan_pages: int = 25, reject_mostly_scanned: bool = True
) -> dict:
    """
    Open the PDF and classify it. Only the first `max_scan_pages` pages are
    scanned so this stays fast for large documents; real ingestion still
    processes every page.

    reject_mostly_scanned: also reject image-heavy PDFs whose text layer is only
    page numbers / headers. Lesson ingest has no OCR, so it uses this. Assessment
    (quiz / diagnostic) uploads pass False: their pipeline can read page images,
    so only PDFs with no text layer at all are rejected there.

    Returns {'ok': True, 'has_images': bool} or
    {'ok': False, 'code': str, 'message': str}.
    """
    import fitz  # PyMuPDF

    bomb_message = check_decompression_bomb(file_bytes)
    if bomb_message:
        return {'ok': False, 'code': 'PDF_TOO_COMPLEX', 'message': bomb_message}

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception:
        return {'ok': False, 'code': 'PDF_UNREADABLE', 'message': PDF_UNREADABLE_MESSAGE}

    try:
        if doc.is_encrypted and not doc.authenticate(""):
            return {
                'ok': False,
                'code': 'PDF_PASSWORD_PROTECTED',
                'message': PDF_PASSWORD_PROTECTED_MESSAGE,
            }

        if doc.page_count == 0:
            return {'ok': False, 'code': 'PDF_EMPTY', 'message': PDF_EMPTY_MESSAGE}

        pages_scanned = 0
        any_text = False
        text_pages = 0
        image_pages = 0
        for i, page in enumerate(doc):
            if i >= max_scan_pages:
                break
            pages_scanned += 1
            chars = len(re.sub(r"\s+", "", page.get_text("text") or ""))
            if chars:
                any_text = True
            if chars >= MIN_PAGE_TEXT_CHARS:
                text_pages += 1
            if page.get_images():
                image_pages += 1

        has_images = image_pages > 0
        if not any_text:
            if has_images:
                return {'ok': False, 'code': 'PDF_SCANNED', 'message': PDF_SCANNED_MESSAGE}
            return {'ok': False, 'code': 'PDF_NO_TEXT', 'message': PDF_NO_TEXT_MESSAGE}

        # Mostly-scanned: most pages are images and almost none carry real text
        # (only page numbers / headers survived in the text layer).
        if (
            reject_mostly_scanned
            and pages_scanned
            and image_pages / pages_scanned >= 0.5
            and text_pages / pages_scanned < MIN_TEXT_PAGE_RATIO
        ):
            return {'ok': False, 'code': 'PDF_SCANNED', 'message': PDF_SCANNED_MESSAGE}

        return {'ok': True, 'has_images': has_images}
    except Exception:
        return {'ok': False, 'code': 'PDF_UNREADABLE', 'message': PDF_UNREADABLE_MESSAGE}
    finally:
        try:
            doc.close()
        except Exception:
            pass


def preflight_check_pdf(
    file_bytes: bytes,
    max_scan_pages: int = 25,
    timeout_seconds: int = 15,
    reject_mostly_scanned: bool = True,
) -> dict:
    """
    Runs `preflight_check_pdf_inner` with a hard wall-clock timeout.

    PyMuPDF is a C extension: a malformed PDF can make it hang inside
    `fz_open_document` with no Python-level exception to catch, which would
    otherwise block the request thread forever. Running it in a daemon
    thread with a bounded `.join()` means a hang costs one leaked thread
    instead of an unresponsive request/worker.
    """
    box = {}

    def _run():
        box['result'] = preflight_check_pdf_inner(file_bytes, max_scan_pages, reject_mostly_scanned)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout_seconds)

    if t.is_alive():
        return {'ok': False, 'code': 'PDF_VALIDATION_TIMEOUT', 'message': PDF_TIMEOUT_MESSAGE}
    return box.get('result') or {
        'ok': False,
        'code': 'PDF_UNREADABLE',
        'message': 'This file could not be validated. Please check the file and upload it again.',
    }
