"""Load full PDF text from RAG chunks for a thread."""
from __future__ import annotations

from app.models.database_models import RAGChunk
from app.utils.db import get_db


def get_thread_full_text(thread_id: str, user_id: int) -> str:
    """Concatenate ordered chunk text for a RAG thread owned by user_id."""
    db = get_db()
    chunks = (
        db.query(RAGChunk)
        .filter(RAGChunk.thread_id == thread_id, RAGChunk.user_id == user_id)
        .order_by(RAGChunk.chunk_index.asc())
        .all()
    )
    parts = [c.text.strip() for c in chunks if c.text and c.text.strip()]
    return "\n\n".join(parts)
