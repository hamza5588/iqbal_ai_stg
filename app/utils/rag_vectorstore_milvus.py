"""
Milvus vector store adapter for RAG.
PostgreSQL stores chunk text (RAGChunk). Milvus stores VECTORS ONLY.
Schema: vector, chunk_id, thread_id, user_id, document_id, chunk_index, page, created_at.
"""
import os
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))


def _collection_name() -> str:
    """Deterministic: one collection per model + dimension."""
    safe_name = EMBEDDING_MODEL_NAME.replace("/", "_").replace("-", "_")
    return f"rag_vectors_{safe_name}_{EMBEDDING_DIM}"


def get_milvus_client():
    """Connect to Milvus."""
    from pymilvus import connections
    alias = "default"
    if not connections.has_connection(alias):
        connections.connect(alias=alias, host=MILVUS_HOST, port=MILVUS_PORT)
    return connections


def ensure_collection() -> str:
    """
    Create Milvus collection if not exists. Idempotent.
    Schema: vector, chunk_id, thread_id, user_id, document_id, chunk_index, page, created_at.
    NO text/source - stored in PostgreSQL only.
    """
    from pymilvus import (
        connections, Collection, FieldSchema, CollectionSchema,
        DataType, utility,
    )
    get_milvus_client()
    coll_name = _collection_name()
    if utility.has_collection(coll_name):
        coll = Collection(coll_name)
        coll.load()
        return coll_name
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
        FieldSchema(name="chunk_id", dtype=DataType.INT64),
        FieldSchema(name="thread_id", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="user_id", dtype=DataType.INT64),
        FieldSchema(name="document_id", dtype=DataType.INT64),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="page", dtype=DataType.INT64),
        FieldSchema(name="created_at", dtype=DataType.INT64),
    ]
    schema = CollectionSchema(fields=fields, description="RAG vectors only")
    coll = Collection(name=coll_name, schema=schema)
    index_params = {"metric_type": "L2", "index_type": "HNSW", "params": {"M": 16, "efConstruction": 256}}
    coll.create_index("vector", index_params)
    coll.load()
    logger.info("Milvus collection ready: %s", coll_name)
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
    Insert vectors into Milvus. Chunk text must already be in PostgreSQL (RAGChunk).
    chunk_ids: RAGChunk.id from PostgreSQL.
    """
    from pymilvus import Collection
    coll_name = ensure_collection()
    coll = Collection(coll_name)
    now = int(datetime.utcnow().timestamp())
    doc_ids = [document_id or 0] * len(vectors)
    created_ats = [now] * len(vectors)
    data = [
        vectors,
        chunk_ids,
        [thread_id] * len(vectors),
        [user_id] * len(vectors),
        doc_ids,
        chunk_indices,
        pages,
        created_ats,
    ]
    coll.insert(data)
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
    from pymilvus import Collection
    coll_name = _collection_name()
    coll = Collection(coll_name)
    safe_tid = str(thread_id).replace('"', '\\"')
    expr = f'thread_id == "{safe_tid}" && user_id == {user_id}'
    results = coll.search(
        data=[query_vector],
        anns_field="vector",
        param={"metric_type": "L2", "params": {"ef": 128}},
        limit=k * 3,
        expr=expr,
        output_fields=["chunk_id", "page", "chunk_index"],
    )
    out = []
    for hits in results:
        for hit in hits:
            out.append({
                "chunk_id": hit.entity.get("chunk_id"),
                "page": hit.entity.get("page", 0),
                "chunk_index": hit.entity.get("chunk_index", 0),
                "score": float(hit.distance),
            })
    out = out[:k]
    max_l2 = float(os.getenv("RAG_MAX_L2_DISTANCE", "1.25"))
    filtered = [r for r in out if float(r.get("score", 999)) <= max_l2]
    if not filtered and out:
        logger.warning(
            "similarity_search: all %d hits above RAG_MAX_L2_DISTANCE=%s; keeping top unfiltered hits as fallback",
            len(out),
            max_l2,
        )
        out = out[: min(3, len(out))]
    else:
        out = filtered
    logger.info(
        "similarity_search: thread_id=%s user_id=%s returned %d hits (max_l2=%s)",
        thread_id,
        user_id,
        len(out),
        max_l2,
    )
    return out


def delete_by_thread(thread_id: str, user_id: int) -> None:
    """Delete all vectors for a thread. Caller must also delete RAGChunk rows in PostgreSQL."""
    from pymilvus import Collection
    coll_name = _collection_name()
    coll = Collection(coll_name)
    safe_tid = str(thread_id).replace('"', '\\"')
    expr = f'thread_id == "{safe_tid}" && user_id == {user_id}'
    coll.delete(expr)
    logger.info("delete_by_thread: deleted vectors for thread_id=%s", thread_id)
