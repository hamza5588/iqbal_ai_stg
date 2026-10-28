import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "utils" / "rag_vectorstore.py"
_SPEC = importlib.util.spec_from_file_location("rag_vectorstore_under_test", _MODULE_PATH)
rag_vectorstore = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(rag_vectorstore)


def test_query_decomposition_is_generic_and_keeps_independent_requirements():
    variants = rag_vectorstore._decompose_retrieval_queries(
        "Calculate the result for version 2026. Show the applicable table, surcharge if any, "
        "exact section, and page number."
    )

    assert variants[0].startswith("Calculate the result")
    assert any("surcharge if any" in item for item in variants)
    assert any("exact section" in item for item in variants)
    assert len(variants) <= 8

    focused = rag_vectorstore._decompose_retrieval_queries(
        "specialized adjustment on employee income version 2026"
    )
    assert "specialized" in focused
    assert "adjustment" in focused
    assert "2026" in focused


def test_hybrid_search_uses_independent_sparse_lists_and_neighbor_expansion(monkeypatch):
    dense = [
        {"chunk_id": 10, "page": 530, "chunk_index": 614, "score": 0.10},
        {"chunk_id": 11, "page": 66, "chunk_index": 75, "score": 0.20},
    ]
    sparse_calls = []

    def fake_sparse(query, thread_id, user_id, limit):
        sparse_calls.append((query, thread_id, user_id, limit))
        if "surcharge" in query.lower():
            return [{"chunk_id": 53, "page": 53, "chunk_index": 57, "score": 0.9}]
        return [{"chunk_id": 10, "page": 530, "chunk_index": 614, "score": 0.8}]

    def fake_neighbors(candidates, thread_id, user_id, radius, limit):
        assert (thread_id, user_id, radius) == ("thread-legal", 7, 8)
        if not any(row["chunk_id"] == 10 for row in candidates):
            return []
        return [
            {"chunk_id": 90, "page": 527, "chunk_index": 611, "score": 0.0}
        ]

    def fake_rerank(query, candidates, limit):
        by_id = {row["chunk_id"]: row for row in candidates}
        assert 90 in by_id
        assert 53 in by_id
        return [by_id[90], by_id[53]][:limit]

    monkeypatch.setattr(rag_vectorstore, "similarity_search", lambda **kwargs: dense)
    monkeypatch.setattr(rag_vectorstore, "postgres_full_text_search", fake_sparse)
    monkeypatch.setattr(rag_vectorstore, "expand_neighbor_chunks", fake_neighbors)
    monkeypatch.setattr(rag_vectorstore, "rerank_candidates", fake_rerank)

    results = rag_vectorstore.hybrid_search(
        query=(
            "Calculate salary income tax for Tax Year 2026. Show the applicable slab, "
            "surcharge if any, exact section, and page number."
        ),
        query_vector=[0.0, 0.0],
        thread_id="thread-legal",
        user_id=7,
        k=8,
    )

    assert [row["chunk_id"] for row in results] == [90, 53]
    assert len(sparse_calls) >= 2
    assert all(call[1:3] == ("thread-legal", 7) for call in sparse_calls)


def test_reranker_fallback_preserves_rrf_order(monkeypatch):
    candidates = [
        {"chunk_id": 1, "page": 1, "chunk_index": 1, "score": 0.5},
        {"chunk_id": 2, "page": 2, "chunk_index": 2, "score": 0.4},
    ]
    monkeypatch.setattr(rag_vectorstore, "fetch_chunks_by_ids", lambda ids: {})
    monkeypatch.setattr(rag_vectorstore, "_get_cross_encoder", lambda: None)

    assert rag_vectorstore.rerank_candidates("question", candidates, 1) == candidates[:1]


def test_hybrid_fallback_keeps_independent_sparse_recall(monkeypatch):
    dense = [
        {"chunk_id": chunk_id, "page": chunk_id, "chunk_index": chunk_id, "score": 0.1}
        for chunk_id in range(1, 33)
    ]
    sparse = {"chunk_id": 900, "page": 53, "chunk_index": 57, "score": 1.0}

    monkeypatch.setattr(rag_vectorstore, "similarity_search", lambda **kwargs: dense)
    monkeypatch.setattr(
        rag_vectorstore,
        "postgres_full_text_search",
        lambda query, thread_id, user_id, limit: [sparse],
    )
    monkeypatch.setattr(rag_vectorstore, "expand_neighbor_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        rag_vectorstore,
        "fetch_chunks_by_ids",
        lambda ids: {
            chunk_id: {
                "text": "independent sparse evidence" if chunk_id == 900 else "dense decoy",
                "page": chunk_id,
                "page_end": chunk_id,
                "chunk_index": chunk_id,
            }
            for chunk_id in ids
        },
    )
    monkeypatch.setattr(rag_vectorstore, "_get_cross_encoder", lambda: None)

    results = rag_vectorstore.hybrid_search(
        query="specialized evidence version 2026",
        query_vector=[0.0],
        thread_id="thread-fallback",
        user_id=7,
        k=8,
    )

    assert 900 in {row["chunk_id"] for row in results}


def test_reranker_attaches_parent_context_ids(monkeypatch):
    candidates = [
        {"chunk_id": 10, "page": 1, "chunk_index": 100, "score": 0.5},
        {"chunk_id": 11, "page": 2, "chunk_index": 101, "score": 0.4},
        {"chunk_id": 12, "page": 3, "chunk_index": 102, "score": 0.3},
    ]

    class FakeModel:
        def predict(self, pairs, batch_size, show_progress_bar):
            return [1.0 - (index * 0.1) for index in range(len(pairs))]

    monkeypatch.setattr(
        rag_vectorstore,
        "fetch_chunks_by_ids",
        lambda ids: {
            chunk_id: {
                "text": f"Document evidence for requested version {chunk_id}",
                "page": chunk_id,
                "chunk_index": 90 + chunk_id,
            }
            for chunk_id in ids
        },
    )
    monkeypatch.setattr(rag_vectorstore, "_get_cross_encoder", lambda: FakeModel())

    result = rag_vectorstore.rerank_candidates("requested version", candidates, 1)

    assert result[0]["chunk_id"] == 10
    assert result[0]["context_chunk_ids"] == [10, 11, 12]


def test_rag_prompt_requires_version_scoped_evidence_and_fail_closed_calculations():
    source = (
        Path(__file__).resolve().parents[1] / "app" / "utils" / "rag_service.py"
    ).read_text(encoding="utf-8")

    assert "RAG_DOCUMENT_GROUNDING_INSTRUCTIONS" in source
    assert "use evidence explicitly applicable to that requested" in source
    assert "never merge their rates or conditions" in source
    assert "do not provide a guessed definitive calculation" in source
    assert 'os.getenv("RAG_LARGE_RETRIEVAL_K", "8")' in source
    assert '"page_end": c.get("page_end", c.get("page", 0))' in source
    assert 'else f"{page_start_num}-{page_end_num}"' in source


def test_production_retrieval_has_no_tax_specific_keyword_router():
    source = _MODULE_PATH.read_text(encoding="utf-8").lower()

    assert "tax year" not in source
    assert "salary" not in source
    assert "surcharge" not in source
    assert "first schedule" not in source
    assert "full-text retrieval" in source
    assert "cross-encoder" in source


def test_milvus_candidate_limit_never_exceeds_hnsw_ef():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "utils"
        / "rag_vectorstore_milvus.py"
    ).read_text(encoding="utf-8")

    assert "search_ef = max(128, int(k))" in source
    assert 'limit=max(1, int(k))' in source
    assert "limit=k * 3" not in source
