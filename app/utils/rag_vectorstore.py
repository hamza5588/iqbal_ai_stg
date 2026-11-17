"""
Unified RAG vector store: Chroma (local) or Milvus (staging/production).
Strict ENV-based selection. No fallback in production.
PostgreSQL (RAGChunk) stores chunk text. Vector DB stores vectors only.
"""
import os
import logging
import re
import threading
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


def query_all_chunks(
    thread_id: str,
    user_id: int,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return a bounded, strictly thread/user-scoped lexical corpus.

    Ingestion already caps the number of chunks per document. ``limit`` is an
    additional runtime guard so hybrid retrieval never performs an unbounded
    in-process scan if an older thread predates those ingest caps.
    """
    from app.utils.db import get_db
    from app.models.database_models import RAGChunk
    try:
        db = get_db()
        query = db.query(
            RAGChunk.id,
            RAGChunk.text,
            RAGChunk.source,
            RAGChunk.page,
            RAGChunk.page_end,
            RAGChunk.chunk_index,
        ).filter(
            RAGChunk.thread_id == thread_id,
            RAGChunk.user_id == user_id,
        ).order_by(RAGChunk.page, RAGChunk.chunk_index)
        if limit is not None and int(limit) > 0:
            query = query.limit(int(limit))
        rows = query.all()
        return [
            {
                "id": r.id,
                "text": r.text,
                "source": r.source or "",
                "page": r.page,
                "page_end": r.page_end,
                "chunk_index": r.chunk_index,
            }
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
            r.id: {
                "text": r.text,
                "source": r.source or "",
                "document_id": r.document_id,
                "page": r.page,
                "page_end": r.page_end,
                "chunk_index": r.chunk_index,
            }
            for r in rows
        }
    except Exception as e:
        logger.warning("fetch_chunks_by_ids error: %s", e)
        return {}


EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))


_LEXICAL_STOP_WORDS = {
    "a", "an", "and", "any", "are", "as", "at", "be", "by", "do", "does", "for", "from",
    "if", "in", "into", "is", "it", "of", "on", "only", "or", "the", "this", "to",
    "use", "using", "what", "when", "where", "which", "with", "without",
    # Generic question/source boilerplate should not consume the small sparse
    # query budget ahead of document-specific versions, entities, or concepts.
    "according", "calculate", "exact", "number", "page", "pdf", "show", "uploaded",
}

_RERANKER = None
_RERANKER_LOAD_FAILED = False
_RERANKER_LOCK = threading.Lock()


def _lexical_tokens(value: str) -> List[str]:
    """Return safe PostgreSQL full-text terms derived only from the user's query."""
    if not value:
        return []
    normalized = re.sub(r"(?<=\d),(?=\d)", "", str(value).lower())
    tokens = re.findall(r"[a-z]+\d*[a-z]*|\d+(?:\.\d+)?", normalized)
    return [token for token in tokens if len(token) > 1 and token not in _LEXICAL_STOP_WORDS]


def _decompose_retrieval_queries(query: str, limit: int = 8) -> List[str]:
    """Create generic subqueries for multi-part questions without domain routing."""
    value = re.sub(r"\s+", " ", str(query or "")).strip()
    if not value:
        return []
    # Do not mistake a thousands separator for a clause boundary.
    split_value = re.sub(r"(?<=\d),(?=\d)", "", value)
    clauses = []
    for position, part in enumerate(re.split(r"[\n.;:!?]+|\s*,\s*", split_value)):
        part = part.strip(" -")
        part_tokens = _lexical_tokens(part)
        if (
            part.lower() != split_value.lower()
            and (len(part_tokens) >= 2 or any(len(token) >= 5 for token in part_tokens))
        ):
            numeric_count = sum(any(char.isdigit() for char in token) for token in part_tokens)
            information_score = (
                (numeric_count * 5.0)
                + min(len(set(part_tokens)), 5)
                + (max((len(token) for token in part_tokens), default=0) / 20.0)
            )
            clauses.append((information_score, -position, part))

    # Long natural-language questions often contain several requirements joined
    # by conjunctions. Split them only when each side remains meaningful.
    for _, parent_position, part in list(clauses):
        for clause in re.split(r"\s+(?:and|also|plus)\s+", part, flags=re.IGNORECASE):
            clause = clause.strip(" -")
            clause_tokens = _lexical_tokens(clause)
            if len(clause_tokens) >= 2:
                numeric_count = sum(any(char.isdigit() for char in token) for token in clause_tokens)
                information_score = (
                    (numeric_count * 5.0)
                    + min(len(set(clause_tokens)), 5)
                    + (max((len(token) for token in clause_tokens), default=0) / 20.0)
                )
                clauses.append((information_score, parent_position, clause))

    # Reserve half the budget for focused clauses and half for atomic high-signal
    # terms. This prevents a long preamble from starving a decisive year/version
    # or concept near the end of the question.
    variants = [value]
    clause_budget = min(len(clauses), max(1, (max(1, int(limit)) - 1) // 2 + 1))
    clauses.sort(reverse=True)
    variants.extend(part for _, _, part in clauses[:clause_budget])

    # Add query-derived atomic terms, prioritizing numbers/versions and then
    # longer distinctive words. No domain vocabulary is encoded here.
    query_terms = list(dict.fromkeys(_lexical_tokens(value)))
    high_signal_terms = [
        term for term in query_terms
        if len(term) >= 5 or any(char.isdigit() for char in term)
    ]
    high_signal_terms.sort(
        key=lambda term: (any(char.isdigit() for char in term), len(term)),
        reverse=True,
    )
    variants.extend(high_signal_terms)

    unique: List[str] = []
    seen = set()
    for variant in variants:
        key = " ".join(_lexical_tokens(variant))
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(variant)
        if len(unique) >= max(1, int(limit)):
            break
    return unique


def postgres_full_text_search(
    query: str,
    thread_id: str,
    user_id: int,
    limit: int,
) -> List[Dict[str, Any]]:
    """Indexed sparse retrieval from PostgreSQL, scoped to one user's thread."""
    from sqlalchemy import text
    from app.utils.db import get_db

    terms = list(dict.fromkeys(_lexical_tokens(query)))[:32]
    if not terms:
        return []
    tsquery = " | ".join(terms)
    sql = text(
        """
        SELECT id AS chunk_id, document_id, page, page_end, chunk_index,
               ts_rank_cd(
                   to_tsvector('simple', coalesce(text, '')),
                   to_tsquery('simple', :tsquery),
                   32
               ) AS score
        FROM rag_chunks
        WHERE thread_id = :thread_id
          AND user_id = :user_id
          AND to_tsvector('simple', coalesce(text, '')) @@ to_tsquery('simple', :tsquery)
        ORDER BY score DESC, id ASC
        LIMIT :result_limit
        """
    )
    try:
        rows = get_db().execute(
            sql,
            {
                "tsquery": tsquery,
                "thread_id": str(thread_id),
                "user_id": int(user_id),
                "result_limit": max(1, int(limit)),
            },
        ).mappings().all()
        return [dict(row) for row in rows]
    except Exception as exc:
        # Local development defaults to SQLite, which has no PostgreSQL tsvector.
        # Dense retrieval remains available; production must expose this warning.
        logger.warning("PostgreSQL full-text retrieval unavailable: %s", exc)
        try:
            get_db().rollback()
        except Exception:
            pass
        return []


def expand_neighbor_chunks(
    candidates: List[Dict[str, Any]],
    thread_id: str,
    user_id: int,
    radius: int = 3,
    limit: int = 96,
) -> List[Dict[str, Any]]:
    """Add surrounding chunks so headings, tables, and continuations stay together."""
    if not candidates or radius <= 0:
        return []
    from sqlalchemy import and_, or_
    from app.utils.db import get_db
    from app.models.database_models import RAGChunk

    seed_ids = [int(row["chunk_id"]) for row in candidates if row.get("chunk_id") is not None]
    if not seed_ids:
        return []
    try:
        db = get_db()
        seeds = db.query(
            RAGChunk.id,
            RAGChunk.document_id,
            RAGChunk.chunk_index,
        ).filter(
            RAGChunk.id.in_(seed_ids),
            RAGChunk.thread_id == str(thread_id),
            RAGChunk.user_id == int(user_id),
        ).all()
        seed_by_id = {int(seed.id): seed for seed in seeds}
        seeds = [seed_by_id[seed_id] for seed_id in seed_ids if seed_id in seed_by_id]
        ranges = [
            and_(
                RAGChunk.document_id == seed.document_id,
                RAGChunk.chunk_index.between(
                    int(seed.chunk_index) - int(radius),
                    int(seed.chunk_index) + int(radius),
                ),
            )
            for seed in seeds
        ]
        if not ranges:
            return []
        rows = db.query(
            RAGChunk.id,
            RAGChunk.document_id,
            RAGChunk.page,
            RAGChunk.page_end,
            RAGChunk.chunk_index,
        ).filter(
            RAGChunk.thread_id == str(thread_id),
            RAGChunk.user_id == int(user_id),
            or_(*ranges),
        ).limit(max(1, int(limit) * 3)).all()
        row_by_position = {
            (row.document_id, int(row.chunk_index)): row
            for row in rows
        }
        # Preserve seed rank and walk outward around each seed. A global ORDER BY
        # chunk_index biased the budget toward early pages and could omit neighbors
        # of the best later-page hit in a long book.
        expanded = []
        seen_ids = set(seed_ids)
        offsets = [0]
        for distance in range(1, int(radius) + 1):
            offsets.extend((-distance, distance))
        for seed in seeds:
            for offset in offsets:
                row = row_by_position.get(
                    (seed.document_id, int(seed.chunk_index) + offset)
                )
                if row is None or int(row.id) in seen_ids:
                    continue
                seen_ids.add(int(row.id))
                expanded.append(
                    {
                        "chunk_id": row.id,
                        "document_id": row.document_id,
                        "page": row.page,
                        "page_end": row.page_end,
                        "chunk_index": row.chunk_index,
                        "score": 0.0,
                    }
                )
                if len(expanded) >= max(1, int(limit)):
                    return expanded
        return expanded
    except Exception as exc:
        logger.warning("Neighbor expansion failed: %s", exc)
        return []


def _get_cross_encoder():
    """Lazily load the configurable second-stage reranker once per worker."""
    global _RERANKER, _RERANKER_LOAD_FAILED
    if _RERANKER is not None or _RERANKER_LOAD_FAILED:
        return _RERANKER
    if os.getenv("RAG_CROSS_ENCODER_ENABLED", "true").lower() not in ("1", "true", "yes"):
        return None
    with _RERANKER_LOCK:
        if _RERANKER is not None or _RERANKER_LOAD_FAILED:
            return _RERANKER
        try:
            from sentence_transformers import CrossEncoder
            model_name = os.getenv(
                "RAG_CROSS_ENCODER_MODEL",
                "cross-encoder/ms-marco-MiniLM-L6-v2",
            )
            _RERANKER = CrossEncoder(
                model_name,
                device="cpu",
                max_length=max(64, int(os.getenv("RAG_RERANK_MAX_LENGTH", "192"))),
            )
            logger.info("Loaded RAG cross-encoder reranker: %s", model_name)
        except Exception as exc:
            _RERANKER_LOAD_FAILED = True
            logger.error("Cross-encoder reranker unavailable; using RRF order: %s", exc)
    return _RERANKER


def preload_cross_encoder() -> bool:
    """Best-effort worker warm-up that preserves a later lazy retry on startup failure."""
    global _RERANKER_LOAD_FAILED
    if os.getenv("RAG_CROSS_ENCODER_PRELOAD", "true").lower() not in ("1", "true", "yes"):
        return False
    failed_before_preload = _RERANKER_LOAD_FAILED
    model = _get_cross_encoder()
    if model is None and not failed_before_preload and _RERANKER_LOAD_FAILED:
        # A transient startup resource spike must not permanently downgrade this
        # worker to RRF ordering. The first real query may retry the normal loader.
        _RERANKER_LOAD_FAILED = False
        logger.warning("Cross-encoder startup preload failed; lazy query-time retry remains enabled")
    return model is not None


def _passage_windows(query: str, text_value: str, max_windows: int = 2) -> List[str]:
    """Select query-focused windows from oversized extracted chunks."""
    text_value = re.sub(r"\s+", " ", str(text_value or "")).strip()
    if not text_value:
        return [""]
    window_chars = max(400, int(os.getenv("RAG_RERANK_WINDOW_CHARS", "650")))
    if len(text_value) <= window_chars:
        return [text_value]
    overlap = min(300, window_chars // 4)
    query_terms = set(_lexical_tokens(query))
    scored = []
    for start in range(0, len(text_value), max(1, window_chars - overlap)):
        passage = text_value[start:start + window_chars]
        passage_terms = set(_lexical_tokens(passage))
        coverage = len(query_terms & passage_terms) / max(len(query_terms), 1)
        scored.append((coverage, -start, passage))
        if start + window_chars >= len(text_value):
            break
    scored.sort(reverse=True)
    return [passage for _, _, passage in scored[:max(1, int(max_windows))]]


def rerank_candidates(
    query: str,
    candidates: List[Dict[str, Any]],
    limit: int,
    thread_id: Optional[str] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Cross-encode only the fused candidate set; fall back cleanly to RRF."""
    if not candidates:
        return []
    candidate_ids = [int(row["chunk_id"]) for row in candidates if row.get("chunk_id") is not None]
    chunk_map = fetch_chunks_by_ids(candidate_ids)

    # Parent-document context is used for scoring, not added blindly to the LLM
    # prompt. A matched heading/continuation can therefore be reranked using an
    # adjacent table or applicability paragraph without multiplying the number
    # of cross-encoder pairs.
    rerank_context_rows: List[Dict[str, Any]] = []
    context_radius = max(0, int(os.getenv("RAG_PARENT_CONTEXT_RADIUS", "12")))
    if thread_id is not None and user_id is not None and context_radius > 0:
        rerank_context_rows = expand_neighbor_chunks(
            candidates,
            str(thread_id),
            int(user_id),
            radius=context_radius,
            limit=max(64, len(candidates) * context_radius * 2),
        )
        context_ids = [
            int(row["chunk_id"])
            for row in rerank_context_rows
            if row.get("chunk_id") is not None
        ]
        if context_ids:
            chunk_map.update(fetch_chunks_by_ids(context_ids))
    model = _get_cross_encoder()
    if model is None:
        return candidates[: max(1, int(limit))]

    pairs = []
    pair_keys = []
    query_variants = _decompose_retrieval_queries(query)
    if not query_variants:
        query_variants = [query]
    context_rows = list(candidates) + rerank_context_rows
    text_by_position = {}
    row_by_position = {}
    for row in context_rows:
        if row.get("chunk_id") is None or row.get("chunk_index") is None:
            continue
        chunk = chunk_map.get(int(row["chunk_id"]), {})
        document_id = row.get("document_id", chunk.get("document_id"))
        text_by_position[(document_id, int(row["chunk_index"]))] = chunk.get("text", "")
        row_by_position[(document_id, int(row["chunk_index"]))] = row
    max_windows = max(1, int(os.getenv("RAG_RERANK_MAX_WINDOWS", "1")))
    for row in candidates:
        chunk_id = row.get("chunk_id")
        chunk = chunk_map.get(int(chunk_id)) if chunk_id is not None else None
        current_text = (chunk or {}).get("text", "")
        chunk_index = row.get("chunk_index")
        document_id = row.get("document_id", (chunk or {}).get("document_id"))
        context_parts = []
        if chunk_index is not None:
            # Query-focused snippets from surrounding chunks give a table its
            # heading/applicability context, and give a matched heading access to
            # a nearby table. The final passage remains bounded below.
            for offset in range(-context_radius, context_radius + 1):
                if offset == 0:
                    continue
                neighbor_text = text_by_position.get(
                    (document_id, int(chunk_index) + offset),
                    "",
                )
                if neighbor_text:
                    context_parts.append(_passage_windows(query, neighbor_text, 1)[0])
        context_parts.append(current_text)
        contextual_text = "\n".join(context_parts)
        for passage in _passage_windows(query, contextual_text, max_windows):
            passage_terms = set(_lexical_tokens(passage))
            aspect_candidates = []
            for variant_index, variant in enumerate(query_variants[1:], start=1):
                variant_terms = set(_lexical_tokens(variant))
                coverage = len(variant_terms & passage_terms) / max(len(variant_terms), 1)
                aspect_candidates.append((coverage, variant_index, variant))
            aspect_candidates.sort(reverse=True)
            selected_variants = [(0, query)]
            if aspect_candidates and aspect_candidates[0][0] > 0:
                _, variant_index, variant = aspect_candidates[0]
                selected_variants.append((variant_index, variant))
            for variant_index, variant in selected_variants:
                pairs.append((variant, passage))
                pair_keys.append((int(chunk_id), variant_index))
    if not pairs:
        return candidates[: max(1, int(limit))]
    try:
        logger.info(
            "Cross-encoder rerank start: candidates=%d pairs=%d max_passage_chars=%d",
            len(candidates),
            len(pairs),
            max((len(pair[1]) for pair in pairs), default=0),
        )
        scores = model.predict(
            pairs,
            batch_size=max(1, int(os.getenv("RAG_RERANK_BATCH_SIZE", "16"))),
            show_progress_bar=False,
        )
        logger.info("Cross-encoder rerank complete: scores=%d", len(scores))
    except Exception as exc:
        logger.error("Cross-encoder prediction failed; using RRF order: %s", exc)
        return candidates[: max(1, int(limit))]

    best_score: Dict[tuple, float] = {}
    for pair_key, score in zip(pair_keys, scores):
        best_score[pair_key] = max(best_score.get(pair_key, float("-inf")), float(score))
    reranked = []
    for rrf_rank, row in enumerate(candidates, start=1):
        item = dict(row)
        chunk_id = int(item["chunk_id"])
        original_score = best_score.get((chunk_id, 0), float("-inf"))
        aspect_scores = [
            score
            for (owner, variant_index), score in best_score.items()
            if owner == chunk_id and variant_index != 0
        ]
        aspect_score = max(aspect_scores) if aspect_scores else original_score
        cross_score = (0.45 * original_score) + (0.55 * aspect_score)
        item["rrf_score"] = float(item.get("score", 0.0))
        item["rerank_score"] = cross_score
        item["original_rerank_score"] = original_score
        item["aspect_rerank_score"] = aspect_score
        item["_variant_scores"] = {
            variant_index: score
            for (owner, variant_index), score in best_score.items()
            if owner == chunk_id and variant_index != 0
        }
        # Cross-encoder is the main judge; RRF is a deterministic tie-breaker.
        item["score"] = cross_score + (0.01 / (60 + rrf_rank))
        reranked.append(item)
    reranked.sort(key=lambda row: row.get("score", float("-inf")), reverse=True)

    # A multi-part question needs evidence for each independently stated clause.
    # Reserve one leader per query-derived aspect, then fill by overall score.
    selected = []
    selected_ids = set()
    aspect_leaders = []
    for variant_index in range(1, len(query_variants)):
        eligible = [
            row for row in reranked
            if variant_index in row.get("_variant_scores", {})
        ]
        if not eligible:
            continue
        leader = max(
            eligible,
            key=lambda row: row["_variant_scores"][variant_index],
        )
        aspect_leaders.append(
            (leader["_variant_scores"][variant_index], variant_index, leader)
        )
    # Do not let formatting-like or low-value clauses consume the entire result
    # set. A small aspect quota protects multi-part recall; the remaining slots
    # are filled by the strongest overall evidence.
    aspect_leaders.sort(key=lambda item: item[0], reverse=True)
    aspect_quota = min(
        max(1, int(limit) // 2),
        max(1, int(os.getenv("RAG_ASPECT_LEADER_QUOTA", "3"))),
    )
    for _, _, leader in aspect_leaders:
        chunk_id = int(leader["chunk_id"])
        if chunk_id in selected_ids:
            continue
        selected.append(leader)
        selected_ids.add(chunk_id)
        if len(selected) >= aspect_quota:
            break
    for row in reranked:
        chunk_id = int(row["chunk_id"])
        if chunk_id in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(chunk_id)
        if len(selected) >= max(1, int(limit)):
            break
    # Aspect leaders protect recall, but the final delivery order should still
    # put the strongest overall evidence first so its full parent window survives
    # downstream context caps.
    selected.sort(key=lambda row: row.get("score", float("-inf")), reverse=True)
    candidate_id_by_index = {
        int(row["chunk_index"]): int(row["chunk_id"])
        for row in candidates
        if row.get("chunk_index") is not None and row.get("chunk_id") is not None
    }
    parent_radius = max(0, int(os.getenv("RAG_PARENT_CONTEXT_RADIUS", "12")))
    context_offsets = [0]
    for distance in range(1, parent_radius + 1):
        context_offsets.extend((-distance, distance))
    for row in selected:
        chunk_index = row.get("chunk_index")
        chunk = chunk_map.get(int(row["chunk_id"]), {})
        document_id = row.get("document_id", chunk.get("document_id"))
        if chunk_index is not None:
            row["context_chunk_ids"] = [
                candidate_id_by_index[int(chunk_index) + offset]
                for offset in context_offsets
                if int(chunk_index) + offset in candidate_id_by_index
            ]
            query_terms = set(_lexical_tokens(query))
            support_candidates = []
            for offset in range(-context_radius, context_radius + 1):
                if offset == 0:
                    continue
                position = (document_id, int(chunk_index) + offset)
                context_row = row_by_position.get(position)
                context_text = text_by_position.get(position, "")
                if not context_row or not context_text:
                    continue
                overlap = query_terms & set(_lexical_tokens(context_text))
                if not overlap:
                    continue
                overlap_score = sum(
                    3.0 if any(char.isdigit() for char in term) else min(len(term), 10) / 10.0
                    for term in overlap
                )
                support_candidates.append(
                    (
                        overlap_score,
                        -abs(offset),
                        int(context_row["chunk_id"]),
                    )
                )
            support_candidates.sort(reverse=True)
            row["supporting_context_chunk_ids"] = [
                chunk_id for _, _, chunk_id in support_candidates[:3]
            ]
        row.pop("_variant_scores", None)
    return selected[: max(1, int(limit))]


def hybrid_search(
    query: str,
    query_vector: List[float],
    thread_id: str,
    user_id: int,
    k: int = 12,
    w_semantic: float = 1.0,
    w_lexical: float = 1.0,
) -> List[Dict[str, Any]]:
    """
    Indexed dense+sparse retrieval, RRF fusion, neighbor expansion, then reranking.
    """
    print("[RAG] hybrid_search running (dense + lexical fusion) thread_id=%s k=%d" % (thread_id, k))
    logger.info(
        "hybrid_search: start query_len=%d thread_id=%s user_id=%s k=%d w_sem=%.2f w_lex=%.2f",
        len(query), thread_id, user_id, k, w_semantic, w_lexical,
    )
    candidate_multiplier = max(2, int(os.getenv("RAG_HYBRID_CANDIDATE_MULTIPLIER", "4")))
    candidate_k = min(64, max(k * candidate_multiplier, int(os.getenv("RAG_HYBRID_MIN_CANDIDATES", "32"))))

    # 1) Dense candidates (over-fetched before final fusion).
    dense_results = similarity_search(
        query_vector=query_vector,
        thread_id=thread_id,
        user_id=user_id,
        k=candidate_k,
    )

    # 2) Independent indexed sparse lists for the original and generic subqueries.
    query_variants = _decompose_retrieval_queries(query)
    lexical_lists = [
        postgres_full_text_search(variant, thread_id, user_id, candidate_k)
        for variant in query_variants
    ]

    # 3) Reciprocal-rank fusion. Raw Milvus distances and PostgreSQL ranks are on
    # incompatible scales, so only ranks are combined.
    candidates: Dict[int, Dict[str, Any]] = {}
    fused_scores: Dict[int, float] = {}
    rrf_constant = max(1, int(os.getenv("RAG_HYBRID_RRF_CONSTANT", "60")))

    for rank, row in enumerate(dense_results, start=1):
        cid = row.get("chunk_id")
        if cid is None:
            continue
        cid = int(cid)
        candidates[cid] = dict(row)
        fused_scores[cid] = fused_scores.get(cid, 0.0) + (w_semantic / (rrf_constant + rank))

    lexical_hits = 0
    for lexical_results in lexical_lists:
        lexical_hits += len(lexical_results)
        for rank, row in enumerate(lexical_results, start=1):
            cid = row.get("chunk_id")
            if cid is None:
                continue
            cid = int(cid)
            candidates[cid] = {**candidates.get(cid, {}), **row}
            fused_scores[cid] = fused_scores.get(cid, 0.0) + (
                w_lexical / (rrf_constant + rank)
            )

    fused: List[Dict[str, Any]] = []
    for cid, row in candidates.items():
        item = dict(row)
        item["chunk_id"] = cid
        item["score"] = fused_scores.get(cid, 0.0)
        fused.append(item)
    fused.sort(key=lambda item: item.get("score", 0.0), reverse=True)

    # 4) Parent/neighbor expansion recovers a table or continuation next to a
    # matching heading. It is structural and therefore works for any PDF domain.
    seed_count = min(len(fused), max(12, k * 4))
    neighbors = expand_neighbor_chunks(
        fused[:seed_count],
        thread_id,
        user_id,
        radius=max(0, int(os.getenv("RAG_NEIGHBOR_RADIUS", "8"))),
        limit=max(candidate_k * 2, 64),
    )
    neighbor_ids = []
    for row in neighbors:
        cid = int(row["chunk_id"])
        neighbor_ids.append(cid)
        if cid not in candidates:
            candidates[cid] = dict(row)
            fused_scores[cid] = 0.0

    ranked_base = []
    for cid, row in candidates.items():
        if cid in neighbor_ids and fused_scores.get(cid, 0.0) == 0.0:
            continue
        item = dict(row)
        item["chunk_id"] = cid
        item["score"] = fused_scores.get(cid, 0.0)
        ranked_base.append(item)
    ranked_base.sort(key=lambda item: item.get("score", 0.0), reverse=True)

    # Guarantee independent-channel recall before filling by fused score. A hit
    # that is rank 1 for one specific clause must not be crowded out merely
    # because generic chunks repeat across several broader query lists.
    priority_ids = []
    priority_seen = set()
    priority_lists = [dense_results[:8]] + [rows[:4] for rows in lexical_lists]
    # Round-robin across channels. If the cross-encoder is unavailable, its
    # documented fallback returns this order, so dense results must not occupy
    # every final slot before an independent sparse list gets one candidate.
    max_priority_depth = max((len(rows) for rows in priority_lists), default=0)
    for rank in range(max_priority_depth):
        for rows in priority_lists:
            if rank >= len(rows):
                continue
            row = rows[rank]
            cid = row.get("chunk_id")
            if cid is None or int(cid) in priority_seen:
                continue
            priority_ids.append(int(cid))
            priority_seen.add(int(cid))
    ranked_by_id = {int(row["chunk_id"]): row for row in ranked_base}
    all_candidates = [
        ranked_by_id[cid]
        for cid in priority_ids
        if cid in ranked_by_id
    ][:candidate_k]
    base_ids = {int(row["chunk_id"]) for row in all_candidates}
    for row in ranked_base:
        cid = int(row["chunk_id"])
        if cid in base_ids:
            continue
        all_candidates.append(row)
        base_ids.add(cid)
        if len(all_candidates) >= candidate_k:
            break

    # Reserve a separate rerank budget for structural neighbors. Otherwise a
    # large dense/sparse union can fill the cap before its adjacent table arrives.
    present_ids = {int(row["chunk_id"]) for row in all_candidates}
    neighbor_budget = max(32, candidate_k)
    for row in neighbors:
        cid = int(row["chunk_id"])
        if cid in present_ids:
            continue
        all_candidates.append(dict(row))
        present_ids.add(cid)
        if len(present_ids) >= candidate_k + neighbor_budget:
            break

    # 5) Accurate second-stage scoring is affordable only after candidate pruning.
    # This is display-only progress; it does not affect retrieval or ranking.
    try:
        from app.utils.chat_progress import set_progress as _set_chat_progress
        _set_chat_progress(thread_id, "Ranking the best matching passages...")
    except Exception:
        pass
    top = rerank_candidates(
        query,
        all_candidates,
        k,
        thread_id=thread_id,
        user_id=user_id,
    )

    # Fetch the final parent windows directly from PostgreSQL. Building context
    # only from the rerank pool can leave a relevant adjacent chunk out when the
    # pool cap is reached (for example, a table six chunks before its matched
    # schedule heading).
    parent_radius = max(0, int(os.getenv("RAG_PARENT_CONTEXT_RADIUS", "12")))
    if top and parent_radius > 0:
        parent_rows = expand_neighbor_chunks(
            top,
            thread_id,
            user_id,
            radius=parent_radius,
            limit=max(k * parent_radius * 2, 64),
        )
        parent_by_index = {
            int(row["chunk_index"]): int(row["chunk_id"])
            for row in parent_rows
            if row.get("chunk_index") is not None and row.get("chunk_id") is not None
        }
        for row in top:
            if row.get("chunk_index") is None or row.get("chunk_id") is None:
                continue
            parent_by_index[int(row["chunk_index"])] = int(row["chunk_id"])
            offsets = [0]
            for distance in range(1, parent_radius + 1):
                offsets.extend((-distance, distance))
            row["context_chunk_ids"] = [
                parent_by_index[int(row["chunk_index"]) + offset]
                for offset in offsets
                if int(row["chunk_index"]) + offset in parent_by_index
            ]

    print(
        "[RAG] hybrid_search done: dense_hits=%d lexical_hits=%d candidates=%d reranked_top_k=%d"
        % (len(dense_results), lexical_hits, len(all_candidates), len(top))
    )
    logger.info(
        "hybrid_search: done dense_hits=%d lexical_hits=%d candidates=%d reranked_top_k=%d thread_id=%s",
        len(dense_results), lexical_hits, len(all_candidates), len(top), thread_id,
    )
    return top
