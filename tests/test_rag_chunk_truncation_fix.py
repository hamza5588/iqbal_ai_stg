"""
Tests for the #19 chunk-truncation fix:
  - the concatenated-split path only engages above the page-count gate
  - it produces no chunk loss on a document that would otherwise breach the cap
  - page_start/page_end metadata correctly maps chunks back to source pages
  - page-range queries return tail content for a large document
"""
import os

import pytest
from langchain_core.documents import Document

from app.utils.rag_service import (
    _should_concat_split,
    _split_docs_concatenated,
    _resolve_ingest_profile,
    _apply_chunk_cap,
)


def _make_docs(num_pages, chars_per_page=400):
    """Build fake PyPDFLoader-style output: one Document per page, 0-indexed
    "page" metadata, with distinguishable per-page text (so we can later
    verify tail content is reachable)."""
    docs = []
    for i in range(num_pages):
        text = f"PAGE-{i}-MARKER " + ("filler word " * (chars_per_page // 12))
        docs.append(Document(page_content=text, metadata={"page": i, "source": "test.pdf"}))
    return docs


# ---------------------------------------------------------------------------
# Gate: only documents above the threshold take the new path
# ---------------------------------------------------------------------------

def test_should_concat_split_below_threshold_stays_on_old_path():
    threshold = int(os.getenv("RAG_CONCAT_SPLIT_PAGE_THRESHOLD", "300"))
    assert _should_concat_split(threshold - 1) is False
    assert _should_concat_split(0) is False
    assert _should_concat_split(50) is False


def test_should_concat_split_at_or_above_threshold_uses_new_path():
    threshold = int(os.getenv("RAG_CONCAT_SPLIT_PAGE_THRESHOLD", "300"))
    assert _should_concat_split(threshold) is True
    assert _should_concat_split(threshold + 500) is True


# ---------------------------------------------------------------------------
# No chunk loss above the threshold, on the measured worst cases from the
# ticket (3412 chunks against a 2000 cap; a 4000-page file losing 50%).
# ---------------------------------------------------------------------------

def test_concat_split_avoids_cap_breach_on_measured_worst_case():
    """
    A sparse, very-many-page document is exactly the failure mode described:
    each page produces ~1 chunk under the old per-page splitter, so the
    chunk count tracks page count and breaches the cap on page count alone.
    The concatenated path must bring this comfortably under the cap.
    """
    num_pages = 4000
    docs = _make_docs(num_pages, chars_per_page=200)  # sparse pages
    profile = _resolve_ingest_profile(file_size_mb=2.0)  # "standard" tier, cap 2000

    assert _should_concat_split(num_pages) is True
    chunks = _split_docs_concatenated(docs, profile)

    max_chunks = profile["max_chunks"]
    assert len(chunks) <= max_chunks, (
        f"concatenated split produced {len(chunks)} chunks, still breaching "
        f"the cap of {max_chunks} on the measured worst case"
    )
    # Sanity: didn't just produce a handful of giant chunks and lose text -
    # every page's marker must be recoverable from *some* chunk.
    combined = "".join(c.page_content for c in chunks)
    for i in (0, num_pages // 2, num_pages - 1):
        assert f"PAGE-{i}-MARKER" in combined


def test_per_page_split_would_have_breached_the_cap():
    """Guard against the test above becoming vacuous: confirm the old
    per-page approach really does breach the cap for this input, so the
    assertion above is testing something real."""
    num_pages = 4000
    docs = _make_docs(num_pages, chars_per_page=200)
    profile = _resolve_ingest_profile(file_size_mb=2.0)
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=profile["chunk_size"],
        chunk_overlap=profile["chunk_overlap"],
        separators=["\n\n", "\n", " ", ""],
    )
    per_page_chunks = splitter.split_documents(docs)
    assert len(per_page_chunks) > profile["max_chunks"]


# ---------------------------------------------------------------------------
# Below the threshold, chunking is untouched: same call, same splitter.
# ---------------------------------------------------------------------------

def test_below_threshold_uses_unchanged_per_page_path():
    num_pages = 10
    assert _should_concat_split(num_pages) is False
    # i.e. ingest_pdf's branch calls splitter.split_documents(docs) exactly
    # as before - nothing about this document's processing changes.


# ---------------------------------------------------------------------------
# Page span metadata
# ---------------------------------------------------------------------------

def test_page_span_metadata_maps_chunks_back_to_source_pages():
    num_pages = 350
    docs = _make_docs(num_pages, chars_per_page=1500)  # denser, multi-chunk pages
    profile = _resolve_ingest_profile(file_size_mb=2.0)

    chunks = _split_docs_concatenated(docs, profile)
    assert len(chunks) > 0

    for c in chunks:
        assert "page_start" in c.metadata and "page_end" in c.metadata
        page_start = c.metadata["page_start"]
        page_end = c.metadata["page_end"]
        assert page_start is not None and page_end is not None
        assert 1 <= page_start <= page_end <= num_pages
        # "page" mirrors the old 0-indexed PyPDFLoader convention, derived
        # from page_start.
        assert c.metadata["page"] == page_start - 1

    # Chunks must appear in non-decreasing page order.
    starts = [c.metadata["page_start"] for c in chunks]
    assert starts == sorted(starts)

    # The very first and very last page must each be covered by at least one
    # chunk - nothing at either edge silently vanished.
    all_pages_covered = set()
    for c in chunks:
        all_pages_covered.update(range(c.metadata["page_start"], c.metadata["page_end"] + 1))
    assert 1 in all_pages_covered
    assert num_pages in all_pages_covered


def test_tail_content_is_reachable_via_page_span_metadata():
    """
    Regression check for the exact symptom in the ticket: late-page content
    in a large document must be reachable. Here that means a chunk whose
    page_start/page_end covers a late page actually contains that page's
    marker text.
    """
    num_pages = 4000
    docs = _make_docs(num_pages, chars_per_page=300)
    profile = _resolve_ingest_profile(file_size_mb=2.0)

    chunks = _split_docs_concatenated(docs, profile)
    late_page = num_pages - 5

    matches = [
        c for c in chunks
        if c.metadata["page_start"] <= late_page <= c.metadata["page_end"]
    ]
    assert matches, f"no chunk's page span covers page {late_page}"
    assert any(f"PAGE-{late_page - 1}-MARKER" in c.page_content for c in matches)


# ---------------------------------------------------------------------------
# Cap-reached warning: fires only when the cap is actually hit, and names
# the correct last-indexed page.
# ---------------------------------------------------------------------------

def test_apply_chunk_cap_noop_when_under_cap():
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    docs = _make_docs(5)
    profile = _resolve_ingest_profile(file_size_mb=2.0)
    chunks = RecursiveCharacterTextSplitter(
        chunk_size=profile["chunk_size"], chunk_overlap=profile["chunk_overlap"],
        separators=["\n\n", "\n", " ", ""],
    ).split_documents(docs)

    kept, warning = _apply_chunk_cap(chunks, max_chunks=profile["max_chunks"])
    assert warning is None
    assert kept == chunks


def test_apply_chunk_cap_fires_and_names_last_indexed_page():
    num_pages = 4000
    docs = _make_docs(num_pages, chars_per_page=200)
    profile = _resolve_ingest_profile(file_size_mb=2.0)
    chunks = _split_docs_concatenated(docs, profile)

    # Force the cap well below the actual chunk count so this test doesn't
    # depend on the concatenated path's exact output size.
    forced_cap = min(50, len(chunks) - 1)
    assert forced_cap > 0, "test setup produced too few chunks to exercise the cap"

    kept, warning = _apply_chunk_cap(chunks, max_chunks=forced_cap)

    assert len(kept) == forced_cap
    assert warning is not None
    assert str(forced_cap) in warning

    expected_last_page = chunks[forced_cap - 1].metadata["page_end"]
    assert f"page {expected_last_page}" in warning


def test_apply_chunk_cap_zero_or_negative_cap_is_noop():
    docs = _make_docs(5)
    profile = _resolve_ingest_profile(file_size_mb=2.0)
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    chunks = RecursiveCharacterTextSplitter(
        chunk_size=profile["chunk_size"], chunk_overlap=profile["chunk_overlap"],
        separators=["\n\n", "\n", " ", ""],
    ).split_documents(docs)

    kept, warning = _apply_chunk_cap(chunks, max_chunks=0)
    assert warning is None
    assert kept == chunks
