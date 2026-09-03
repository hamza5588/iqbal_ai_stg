"""
Unified RAG vector store: Chroma (local) or Milvus (staging/production).
Strict ENV-based selection. No fallback in production.
PostgreSQL (RAGChunk) stores chunk text. Vector DB stores vectors only.
"""
import os
import logging
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

ENV = os.getenv("ENV", "local").lower()
USE_CHROMA_LOCAL = os.getenv("USE_CHROMA_LOCAL", "false").lower() in ("true", "1")
_USE_CHROMA = ENV == "local" or USE_CHROMA_LOCAL
_BACKEND: Optional[str] = None


def _resolve_backend() -> str:
    """Strict: local → Chroma, staging/production → Milvus. No fallback."""
    global _BACKEND
    if _BACKEND:
        return _BACKEND
    if _USE_CHROMA:
        _BACKEND = "chroma"
        logger.info("RAG vector store: Chroma (local)")
    else:
        _BACKEND = "milvus"
        logger.info("RAG vector store: Milvus (staging/production)")
    return _BACKEND


def _module():
    if _resolve_backend() == "chroma":
        from app.utils import rag_vectorstore_chroma
        return rag_vectorstore_chroma
    from app.utils import rag_vectorstore_milvus
    return rag_vectorstore_milvus


def ensure_collection() -> str:
    """Idempotent. Production: fails if Milvus unreachable."""
    return _module().ensure_collection()


def insert_chunks(
    vectors: List[List[float]],
    thread_id: str,
    user_id: int,
    document_id: Optional[int],
    chunk_ids: List[int],
    pages: List[int],
    chunk_indices: List[int],
) -> int:
    """Insert vectors. Chunk text must already be in PostgreSQL RAGChunk."""
    return _module().insert_chunks(
        vectors, thread_id, user_id, document_id, chunk_ids, pages, chunk_indices
    )


def similarity_search(
    query_vector: List[float],
    thread_id: str,
    user_id: int,
    k: int = 12,
) -> List[Dict[str, Any]]:
    """Returns [{chunk_id, page, chunk_index, score}]. Caller fetches text from PostgreSQL."""
    return _module().similarity_search(query_vector, thread_id, user_id, k)


def delete_by_thread(thread_id: str, user_id: int) -> None:
    """Delete vectors. Caller must delete RAGChunk rows in PostgreSQL."""
    return _module().delete_by_thread(thread_id, user_id)


def query_chunks_by_page(thread_id: str, user_id: int, page: int) -> List[Dict[str, Any]]:
    """
    Query chunks from PostgreSQL (source of truth for text) that cover `page`.

    Uses [page, page_end] overlap rather than exact equality: a chunk
    produced by the concatenated-split path (large page-count documents) can
    span multiple source pages, so "page == page" alone would miss it for
    any page after its first. page_end always equals page for chunks that
    don't span multiple pages, so this is equivalent to the old exact match
    for every pre-existing chunk. Returns [{text, source, page, page_end, chunk_index}].
    """
    from app.utils.db import get_db
    from app.models.database_models import RAGChunk
    try:
        db = get_db()
        rows = db.query(RAGChunk).filter(
            RAGChunk.thread_id == thread_id,
            RAGChunk.user_id == user_id,
            RAGChunk.page <= page,
            RAGChunk.page_end >= page,
        ).order_by(RAGChunk.chunk_index).all()
        return [
            {"text": r.text, "source": r.source or "", "page": r.page, "page_end": r.page_end, "chunk_index": r.chunk_index}
            for r in rows
        ]
    except Exception as e:
        logger.warning("query_chunks_by_page error: %s", e)
        return []


def query_chunks_by_page_range(thread_id: str, user_id: int, start_page: int, end_page: int) -> List[Dict[str, Any]]:
    """
    Query every chunk from PostgreSQL whose [page, page_end] span overlaps
    [start_page, end_page] (inclusive) - not just chunks whose single `page`
    value falls in range, so a concatenated-split chunk covering e.g. pages
    340-345 is still returned for a query range of (343, 344).
    No top-k cap: used for exhaustive section-based retrieval where truncation would drop content.
    Returns [{text, source, page, page_end, chunk_index}] ordered by page, chunk_index.
    """
    from app.utils.db import get_db
    from app.models.database_models import RAGChunk
    try:
        db = get_db()
        rows = db.query(RAGChunk).filter(
            RAGChunk.thread_id == thread_id,
            RAGChunk.user_id == user_id,
            RAGChunk.page <= end_page,
            RAGChunk.page_end >= start_page,
        ).order_by(RAGChunk.page, RAGChunk.chunk_index).all()
        return [
            {"text": r.text, "source": r.source or "", "page": r.page, "page_end": r.page_end, "chunk_index": r.chunk_index}
            for r in rows
        ]
    except Exception as e:
        logger.warning("query_chunks_by_page_range error: %s", e)
        return []


def query_all_chunks(thread_id: str, user_id: int) -> List[Dict[str, Any]]:
    """Query all chunks from PostgreSQL. Returns [{text, source, page, chunk_index}]."""
    from app.utils.db import get_db
    from app.models.database_models import RAGChunk
    try:
        db = get_db()
        rows = db.query(RAGChunk).filter(
            RAGChunk.thread_id == thread_id,
            RAGChunk.user_id == user_id,
        ).order_by(RAGChunk.page, RAGChunk.chunk_index).all()
        return [
            {"text": r.text, "source": r.source or "", "page": r.page, "chunk_index": r.chunk_index}
            for r in rows
        ]
    except Exception as e:
        logger.warning("query_all_chunks error: %s", e)
        return []


def fetch_chunks_by_ids(chunk_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Fetch chunk text from PostgreSQL by chunk_id. Returns {chunk_id: {text, source, page, chunk_index}}."""
    if not chunk_ids:
        return {}
    from app.utils.db import get_db
    from app.models.database_models import RAGChunk
    try:
        db = get_db()
        rows = db.query(RAGChunk).filter(RAGChunk.id.in_(chunk_ids)).all()
        return {
            r.id: {"text": r.text, "source": r.source or "", "page": r.page, "chunk_index": r.chunk_index}
            for r in rows
        }
    except Exception as e:
        logger.warning("fetch_chunks_by_ids error: %s", e)
        return {}


EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))


def _simple_lexical_score(query: str, text: str) -> float:
    """
    Very lightweight lexical scoring: counts overlap of cleaned tokens.
    This is NOT full BM25, but gives a useful keyword signal for hybrid scoring
    without requiring additional DB indexes.
    """
    if not query or not text:
        return 0.0
    import re

    # Lowercase, split on non-word chars, drop very short tokens
    def _tokens(s: str):
        return [t for t in re.split(r"\W+", s.lower()) if len(t) > 2]

    q_tokens = set(_tokens(query))
    if not q_tokens:
        return 0.0
    t_tokens = _tokens(text)
    if not t_tokens:
        return 0.0

    overlap = sum(1 for tok in t_tokens if tok in q_tokens)
    # Normalize by text length to avoid bias towards huge chunks
    return overlap / max(len(t_tokens), 1)


def hybrid_search(
    query: str,
    query_vector: List[float],
    thread_id: str,
    user_id: int,
    k: int = 12,
    w_semantic: float = 0.7,
    w_lexical: float = 0.3,
) -> List[Dict[str, Any]]:
    """
    Hybrid retrieval: combines dense vector similarity search with a simple lexical signal.

    - Runs existing similarity_search() to get semantic hits (from Milvus/Chroma).
    - Uses query_all_chunks() to compute a per-chunk lexical score from raw text.
    - Fuses scores via weighted sum and returns top-k chunks with the same shape
      as similarity_search(): [{chunk_id, page, chunk_index, score}].
    """
    print("[RAG] hybrid_search running (dense + lexical fusion) thread_id=%s k=%d" % (thread_id, k))
    logger.info(
        "hybrid_search: start query_len=%d thread_id=%s user_id=%s k=%d w_sem=%.2f w_lex=%.2f",
        len(query), thread_id, user_id, k, w_semantic, w_lexical,
    )
    # 1) Dense similarity search
    dense_results = similarity_search(
        query_vector=query_vector,
        thread_id=thread_id,
        user_id=user_id,
        k=k,
    )

    # 2) Get all chunks for this thread/user with text for lexical scoring
    #    Note: this only returns text + metadata, not chunk_id, so we do a second
    #    lightweight query by IDs for the dense hits to attach IDs to text.
    from app.utils.db import get_db
    from app.models.database_models import RAGChunk

    db = None
    chunk_text_by_id: Dict[int, str] = {}
    try:
        db = get_db()
        dense_ids = [r["chunk_id"] for r in dense_results if r.get("chunk_id") is not None]
        if dense_ids:
            rows = (
                db.query(RAGChunk.id, RAGChunk.text)
                .filter(
                    RAGChunk.id.in_(dense_ids),
                )
                .all()
            )
            chunk_text_by_id = {row[0]: row[1] or "" for row in rows}
    except Exception as e:
        logger.warning("hybrid_search: error loading chunk texts for lexical scoring: %s", e)

    # 3) Compute lexical scores for chunks we have text for
    lexical_scores: Dict[int, float] = {}
    for cid, text in chunk_text_by_id.items():
        lexical_scores[cid] = _simple_lexical_score(query, text)

    # 4) Normalize scores and compute weighted fusion
    #    Dense scores from Milvus are distances; smaller is better, so invert.
    dense_scores: Dict[int, float] = {}
    for r in dense_results:
        cid = r.get("chunk_id")
        dist = float(r.get("score", 0.0))
        if cid is None:
            continue
        dense_scores[cid] = 1.0 / (1.0 + dist)

    def _normalize(d: Dict[int, float]) -> Dict[int, float]:
        if not d:
            return {}
        vals = list(d.values())
        v_min, v_max = min(vals), max(vals)
        if v_max == v_min:
            return {k: 1.0 for k in d.keys()}
        return {k: (v - v_min) / (v_max - v_min) for k, v in d.items()}

    dense_norm = _normalize(dense_scores)
    lexical_norm = _normalize(lexical_scores)

    combined: Dict[int, Dict[str, Any]] = {}
    for r in dense_results:
        cid = r.get("chunk_id")
        if cid is None:
            continue
        combined[cid] = {
            "chunk_id": cid,
            "page": r.get("page", 0),
            "chunk_index": r.get("chunk_index", 0),
        }

    all_ids = set(dense_norm.keys()) | set(lexical_norm.keys())
    out: List[Dict[str, Any]] = []
    for cid in all_ids:
        sem = dense_norm.get(cid, 0.0)
        lex = lexical_norm.get(cid, 0.0)
        final_score = w_semantic * sem + w_lexical * lex
        base = combined.get(cid, {"chunk_id": cid, "page": 0, "chunk_index": 0})
        base["score"] = final_score
        out.append(base)

    # Sort descending by fused score (higher is better) and take top-k
    out.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    top = out[:k]
    print("[RAG] hybrid_search done: dense_hits=%d fused_top_k=%d" % (len(dense_results), len(top)))
    logger.info(
        "hybrid_search: done dense_hits=%d chunks_with_lexical=%d fused_top_k=%d thread_id=%s",
        len(dense_results), len(lexical_scores), len(top), thread_id,
    )
    return top
