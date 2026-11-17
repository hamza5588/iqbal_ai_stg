"""Scoped retrieval for Scenario-Based Assessments.

Every query is filtered by (assessment_id, doc_type) and restricted to chunks of
ACTIVE, READY documents, so:
* learning content, sample questions and reference answers are never mixed;
* a replaced document version is never retrieved again;
* nothing outside the assessment (other teachers / students) can be reached.

Scoring: cosine similarity on stored embeddings when both sides have vectors,
blended with a lexical overlap signal (also the full fallback when embeddings are
unavailable).
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

from app.models.scenario_models import ScenarioChunk, ScenarioDocument
from app.services.scenario.common import loads, tokens
from app.utils.db import get_db

logger = logging.getLogger(__name__)


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _lexical(query_tokens: set, text: str) -> float:
    if not query_tokens:
        return 0.0
    doc_tokens = set(tokens(text))
    if not doc_tokens:
        return 0.0
    return len(query_tokens & doc_tokens) / math.sqrt(len(query_tokens) * len(doc_tokens))


def embed_query(text: str) -> Optional[List[float]]:
    try:
        import os

        if os.getenv("SBA_DISABLE_EMBEDDINGS", "").lower() in ("1", "true", "yes"):
            return None
        from app.utils.rag_service import get_rag_embeddings

        return [float(x) for x in get_rag_embeddings().embed_query(text)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("SBA query embedding failed (lexical only): %s", exc)
        return None


def active_chunks(assessment_id: int, doc_type: str) -> List[ScenarioChunk]:
    db = get_db()
    return (
        db.query(ScenarioChunk)
        .join(ScenarioDocument, ScenarioDocument.id == ScenarioChunk.document_id)
        .filter(
            ScenarioChunk.assessment_id == assessment_id,
            ScenarioChunk.doc_type == doc_type,
            ScenarioDocument.assessment_id == assessment_id,
            ScenarioDocument.doc_type == doc_type,
            ScenarioDocument.is_active.is_(True),
            ScenarioDocument.status == "ready",
        )
        .order_by(ScenarioChunk.document_id, ScenarioChunk.chunk_index)
        .all()
    )


def retrieve(assessment_id: int, doc_type: str, query: str, k: int = 6) -> List[Dict]:
    chunks = active_chunks(assessment_id, doc_type)
    if not chunks:
        return []
    qvec = embed_query(query) if any(c.embedding_json for c in chunks) else None
    qtok = set(tokens(query))
    scored = []
    for c in chunks:
        lex = _lexical(qtok, f"{c.heading or ''} {c.text}")
        vec = loads(c.embedding_json) if qvec is not None else None
        score = 0.75 * _cosine(qvec, vec) + 0.25 * lex if vec else lex
        scored.append((score, c))
    scored.sort(key=lambda t: (-t[0], t[1].document_id, t[1].chunk_index))
    return [to_ref(c, s) for s, c in scored[:k]]


def spread_sample(assessment_id: int, doc_type: str, k: int = 10) -> List[Dict]:
    """Evenly spaced chunks across all active documents (coverage for generation)."""
    chunks = active_chunks(assessment_id, doc_type)
    if len(chunks) <= k:
        return [to_ref(c, 0.0) for c in chunks]
    step = len(chunks) / k
    return [to_ref(chunks[int(i * step)], 0.0) for i in range(k)]


def to_ref(c: ScenarioChunk, score: float) -> Dict:
    return {
        "chunk_id": c.id,
        "document_id": c.document_id,
        "doc_type": c.doc_type,
        "doc_version": c.doc_version,
        "page_start": c.page_start,
        "page_end": c.page_end,
        "heading": c.heading,
        "text": c.text,
        "score": round(float(score), 4),
    }


def format_excerpts(refs: List[Dict], max_chars: int = 9000) -> str:
    out, total = [], 0
    for r in refs:
        pages = r["page_start"] if r["page_start"] == r["page_end"] else f"{r['page_start']}-{r['page_end']}"
        block = f"[id={r['chunk_id']} | page {pages}{' | ' + r['heading'] if r.get('heading') else ''}]\n{r['text']}"
        if total + len(block) > max_chars:
            break
        out.append(block)
        total += len(block)
    return "\n\n".join(out) if out else "(none available)"
