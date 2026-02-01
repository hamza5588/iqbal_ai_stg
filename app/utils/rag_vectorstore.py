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
    """Query chunks from PostgreSQL (source of truth for text). Returns [{text, source, page, chunk_index}]."""
    from app.utils.db import get_db
    from app.models.database_models import RAGChunk
    try:
        db = get_db()
        rows = db.query(RAGChunk).filter(
            RAGChunk.thread_id == thread_id,
            RAGChunk.user_id == user_id,
            RAGChunk.page == page,
        ).order_by(RAGChunk.chunk_index).all()
        return [
            {"text": r.text, "source": r.source or "", "page": r.page, "chunk_index": r.chunk_index}
            for r in rows
        ]
    except Exception as e:
        logger.warning("query_chunks_by_page error: %s", e)
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
