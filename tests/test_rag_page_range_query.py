"""
Tests for the page_end overlap fix in app/utils/rag_vectorstore.py
(query_chunks_by_page / query_chunks_by_page_range), which #19's
concatenated-split path depends on: a chunk that spans multiple source
pages must still be found by an exact-page or page-range query that only
touches part of its span - including the tail of a large document.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database_models import Base, RAGThread, RAGChunk


@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RAGThread.__table__, RAGChunk.__table__])
    Session = sessionmaker(bind=engine)
    session = Session()

    # query_chunks_by_page / query_chunks_by_page_range call get_db() lazily
    # (imported inside the function body), so patch it where it's looked up.
    import app.utils.db as db_module
    monkeypatch.setattr(db_module, "get_db", lambda: session)

    yield session
    session.close()


def _seed_thread_and_chunks(session, thread_id="t1", user_id=1):
    thread = RAGThread(user_id=user_id, thread_id=thread_id, name="Test thread")
    session.add(thread)
    session.commit()

    rows = [
        # A normal, single-page chunk (page_end == page), same as every
        # pre-existing chunk before this fix.
        {"chunk_index": 0, "page": 0, "page_end": 0, "text": "front matter"},
        {"chunk_index": 1, "page": 1, "page_end": 1, "text": "chapter 1 intro"},
        # A concatenated-split chunk spanning multiple pages (0-indexed
        # 339..344, i.e. 1-indexed pages 340-345).
        {"chunk_index": 2, "page": 339, "page_end": 344, "text": "spans pages 340-345"},
        # The tail of a large document - must remain reachable.
        {"chunk_index": 3, "page": 3998, "page_end": 3999, "text": "tail content page 3999-4000"},
    ]
    for r in rows:
        session.add(RAGChunk(thread_id=thread_id, user_id=user_id, document_id=None, source="t.pdf", **r))
    session.commit()


def test_query_by_page_finds_multi_page_chunk_from_any_covered_page(db_session):
    from app.utils.rag_vectorstore import query_chunks_by_page

    _seed_thread_and_chunks(db_session)

    # Page 343 (1-indexed) falls inside the chunk's [340, 345] span but is
    # not its first page - exact-equality matching would have missed it.
    results = query_chunks_by_page("t1", 1, page=342)  # 0-indexed page 342 == 1-indexed 343
    assert any("spans pages 340-345" in r["text"] for r in results)


def test_query_by_page_range_returns_tail_chunk_of_large_document(db_session):
    from app.utils.rag_vectorstore import query_chunks_by_page_range

    _seed_thread_and_chunks(db_session)

    results = query_chunks_by_page_range("t1", 1, start_page=3995, end_page=4000)
    assert any("tail content" in r["text"] for r in results)
    # And it must not pull in unrelated early-document chunks.
    assert not any("front matter" in r["text"] for r in results)


def test_query_by_page_range_finds_multi_page_chunk_overlapping_only_the_tail_of_its_span(db_session):
    from app.utils.rag_vectorstore import query_chunks_by_page_range

    _seed_thread_and_chunks(db_session)

    # Range only touches the last couple of pages of the chunk's [340, 345]
    # span - a naive "page BETWEEN start AND end" filter on the single
    # `page`=339 value alone would miss this.
    results = query_chunks_by_page_range("t1", 1, start_page=344, end_page=350)
    assert any("spans pages 340-345" in r["text"] for r in results)


def test_query_by_page_range_excludes_non_overlapping_chunks(db_session):
    from app.utils.rag_vectorstore import query_chunks_by_page_range

    _seed_thread_and_chunks(db_session)

    results = query_chunks_by_page_range("t1", 1, start_page=500, end_page=600)
    assert results == []
