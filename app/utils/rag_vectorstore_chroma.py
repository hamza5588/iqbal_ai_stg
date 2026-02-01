"""
Chroma vector store adapter for RAG (local dev only).
PostgreSQL stores chunk text (RAGChunk). Chroma stores VECTORS ONLY.
Same API as Milvus adapter.
"""
import os
import logging
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
CHROMA_PATH = os.getenv("CHROMA_PERSIST_DIR", str(Path(__file__).parent.parent.parent / "chroma_data"))


def _collection_name() -> str:
    safe_name = EMBEDDING_MODEL_NAME.replace("/", "_").replace("-", "_")
    return f"rag_vectors_{safe_name}_{EMBEDDING_DIM}"


def _get_client():
    import chromadb
    from chromadb.config import Settings
    Path(CHROMA_PATH).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))


def ensure_collection() -> str:
    """Create or get Chroma collection. Idempotent."""
    client = _get_client()
    coll_name = _collection_name()
    client.get_or_create_collection(name=coll_name, metadata={"dimension": EMBEDDING_DIM})
    logger.info("Chroma collection ready: %s", coll_name)
    return coll_name


def insert_chunks(
    vectors: List[List[float]],
    thread_id: str,
    user_id: int,
    document_id: Optional[int],
    chunk_ids: List[int],
    pages: List[int],
    chunk_indices: List[int],
) -> int:
    """
    Insert vectors into Chroma. Chunk text must already be in PostgreSQL (RAGChunk).
    chunk_ids: RAGChunk.id from PostgreSQL.
    """
    client = _get_client()
    coll_name = ensure_collection()
    coll = client.get_collection(name=coll_name)
    ids = [str(uuid.uuid4()) for _ in vectors]
    metadatas = [
        {
            "chunk_id": cid,
            "thread_id": thread_id,
            "user_id": user_id,
            "document_id": document_id or 0,
            "chunk_index": ci,
            "page": p,
            "created_at": int(datetime.utcnow().timestamp()),
        }
        for cid, ci, p in zip(chunk_ids, chunk_indices, pages)
    ]
    coll.add(ids=ids, embeddings=vectors, metadatas=metadatas)
    logger.info("insert_chunks: inserted %d vectors thread_id=%s user_id=%s", len(vectors), thread_id, user_id)
    return len(vectors)


def similarity_search(
    query_vector: List[float],
    thread_id: str,
    user_id: int,
    k: int = 12,
) -> List[Dict[str, Any]]:
    """
    Vector similarity search. Returns chunk_ids, scores, page, chunk_index.
    Caller fetches text from PostgreSQL RAGChunk.
    """
    client = _get_client()
    coll_name = _collection_name()
    coll = client.get_collection(name=coll_name)
    results = coll.query(
        query_embeddings=[query_vector],
        n_results=min(k * 3, 100),
        where={"$and": [{"thread_id": thread_id}, {"user_id": user_id}]},
        include=["metadatas", "distances"],
    )
    out = []
    if results and results.get("ids") and results["ids"][0]:
        for i in range(len(results["ids"][0])):
            meta = results["metadatas"][0][i] if results.get("metadatas") else {}
            dist = results["distances"][0][i] if results.get("distances") else 0.0
            out.append({
                "chunk_id": meta.get("chunk_id"),
                "page": meta.get("page", 0),
                "chunk_index": meta.get("chunk_index", 0),
                "score": float(dist),
            })
    out = out[:k]
    logger.info("similarity_search: thread_id=%s user_id=%s returned %d hits", thread_id, user_id, len(out))
    return out


def delete_by_thread(thread_id: str, user_id: int) -> None:
    """Delete all vectors for a thread. Caller must also delete RAGChunk rows in PostgreSQL."""
    client = _get_client()
    coll_name = _collection_name()
    coll = client.get_collection(name=coll_name)
    results = coll.get(
        where={"$and": [{"thread_id": thread_id}, {"user_id": user_id}]},
        include=[],
        limit=10000,
    )
    if results and results.get("ids"):
        coll.delete(ids=results["ids"])
        logger.info("delete_by_thread: deleted %d vectors for thread_id=%s", len(results["ids"]), thread_id)
