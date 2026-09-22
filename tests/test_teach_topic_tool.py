"""
Tests for teach_topic_tool (exhaustive, section-based retrieval for lecture/topic requests).

Problem: rag_tool does a single top-k semantic search (k=6 by default), so when a teacher
asks to "Create a lecture on Quadratic Equations" and the topic is covered across several
headings/sections of the document, content in sections beyond the top-k window is silently
missed.

Fix: teach_topic_tool (app/utils/rag_service.py) reuses the existing heading cache
(_get_thread_topics, shared with list_topics_whole_doc_tool) to find every heading matching
the requested topic, derives each matched heading's page range from heading order (its page
up to the page before the next heading), and pulls EVERY chunk in that range straight from
PostgreSQL (query_chunks_by_page_range) - no top-k cap.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import pytest

rag_service = pytest.importorskip("app.utils.rag_service")


def _topics(*pairs):
    """Build a _get_thread_topics()-shaped result from (heading, page) pairs."""
    return {
        "thread_id": "user_1_abc",
        "topics": [{"topic": h, "page": p} for h, p in pairs],
        "topics_count": len(pairs),
        "method": "db_heading_cache",
        "chunks_scanned": 20,
    }


def _chunks_for_range(chunks_by_page):
    """Fake query_chunks_by_page_range: returns rows for pages in [start, end]."""
    def _fn(thread_id, user_id, start_page, end_page):
        rows = []
        for page in range(start_page, end_page + 1):
            for text in chunks_by_page.get(page, []):
                rows.append({"text": text, "source": "doc.pdf", "page": page, "chunk_index": 0})
        return rows
    return _fn


def test_empty_topic_returns_error():
    result = rag_service.teach_topic_tool.invoke({"topic": "", "thread_id": "user_1_abc"})
    assert "error" in result


def test_missing_thread_id_returns_error():
    result = rag_service.teach_topic_tool.invoke({"topic": "Quadratic Equations", "thread_id": ""})
    assert "error" in result


def test_no_headings_available_returns_error_not_crash(monkeypatch):
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "_get_thread_topics", lambda tid: _topics())
    monkeypatch.setattr(rag_service, "_try_semantic_chunks_for_query", lambda *a, **k: [])

    result = rag_service.teach_topic_tool.invoke({"topic": "Quadratic Equations", "thread_id": "user_1_abc"})
    assert "error" in result
    assert result["matched_sections"] == []


def test_matches_headings_and_retrieves_all_chunks_in_range(monkeypatch):
    """Core exhaustive-retrieval behavior: every chunk in the matched section's page range comes back."""
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(
        rag_service, "_get_thread_topics",
        lambda tid: _topics(
            ("Introduction", 1),
            ("Quadratic Equations", 5),
            ("Quadratic Formula", 8),
            ("Factoring", 12),
            ("Summary", 15),
        ),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_metadata_from_db",
        lambda tid: {"num_pages": 18, "filename": "All_merged.pdf"},
    )
    monkeypatch.setattr(
        "app.utils.rag_vectorstore.query_chunks_by_page_range",
        _chunks_for_range({
            5: ["chunk on quadratic equations p5"], 6: ["chunk p6"], 7: ["chunk p7"],
            8: ["chunk on quadratic formula p8"], 9: ["chunk p9"], 10: ["chunk p10"], 11: ["chunk p11"],
        }),
    )

    result = rag_service.teach_topic_tool.invoke({"topic": "Quadratic Equations", "thread_id": "user_1_abc"})

    headings_matched = {s["heading"] for s in result["matched_sections"]}
    assert "Quadratic Equations" in headings_matched
    assert "Quadratic Formula" in headings_matched
    # Section boundaries: "Quadratic Equations" (page 5) must stop the page before "Quadratic
    # Formula" (page 8) starts - i.e. pages 5-7, not bleeding into the next section.
    qe_section = next(s for s in result["matched_sections"] if s["heading"] == "Quadratic Equations")
    assert qe_section["page_start"] == 5
    assert qe_section["page_end"] == 7
    assert qe_section["chunks_found"] == 3
    # No silent truncation for a normal-sized document.
    assert result["truncated"] is False
    assert result["total_chunks_retrieved"] == 7


def test_no_matches_returns_helpful_message(monkeypatch):
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(
        rag_service, "_get_thread_topics",
        lambda tid: _topics(("Photosynthesis", 1), ("Cell Division", 5)),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_metadata_from_db",
        lambda tid: {"num_pages": 10, "filename": "biology.pdf"},
    )

    result = rag_service.teach_topic_tool.invoke({"topic": "Quadratic Equations", "thread_id": "user_1_abc"})
    assert result["matched_sections"] == []
    assert "message" in result


def test_partial_overlap_reported_as_related_not_covered(monkeypatch):
    """A heading that shares fewer than half the topic's keywords surfaces as a suggestion, not a silent drop."""
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(
        rag_service, "_get_thread_topics",
        lambda tid: _topics(
            ("Quadratic Equations", 5),  # 2/3 token overlap with the topic below -> matched
            ("Quadratic Concepts Overview", 20),  # 1/3 token overlap -> related, not covered
        ),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_metadata_from_db",
        lambda tid: {"num_pages": 25, "filename": "All_merged.pdf"},
    )
    monkeypatch.setattr(
        "app.utils.rag_vectorstore.query_chunks_by_page_range",
        _chunks_for_range({5: ["chunk p5"]}),
    )

    result = rag_service.teach_topic_tool.invoke(
        {"topic": "Quadratic Equations Basics", "thread_id": "user_1_abc"}
    )
    matched_headings = {s["heading"] for s in result["matched_sections"]}
    related_headings = {r["heading"] for r in result["related_not_covered"]}
    assert "Quadratic Equations" in matched_headings
    assert "Quadratic Concepts Overview" in related_headings


def test_repeated_heading_across_merged_documents_stays_as_separate_sections(monkeypatch):
    """
    All_merged.pdf mixes multiple lesson-plan docs: the same heading can appear at several
    pages. Each occurrence must become its own section (own page range, own chunk list) -
    never merged into one blob that mixes unrelated documents' content.
    """
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(
        rag_service, "_get_thread_topics",
        lambda tid: _topics(
            ("Quadratic Equations", 3),
            ("Review", 6),
            ("Quadratic Equations", 40),
            ("Review", 44),
        ),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_metadata_from_db",
        lambda tid: {"num_pages": 50, "filename": "All_merged.pdf"},
    )
    monkeypatch.setattr(
        "app.utils.rag_vectorstore.query_chunks_by_page_range",
        _chunks_for_range({
            3: ["doc1 chunk p3"], 4: ["doc1 chunk p4"], 5: ["doc1 chunk p5"],
            40: ["doc2 chunk p40"], 41: ["doc2 chunk p41"], 42: ["doc2 chunk p42"], 43: ["doc2 chunk p43"],
        }),
    )

    result = rag_service.teach_topic_tool.invoke({"topic": "Quadratic Equations", "thread_id": "user_1_abc"})
    qe_sections = [s for s in result["matched_sections"] if s["heading"] == "Quadratic Equations"]
    assert len(qe_sections) == 2
    ranges = sorted((s["page_start"], s["page_end"]) for s in qe_sections)
    assert ranges == [(3, 5), (40, 43)]
    # Content from the two occurrences must not be mixed into a single section.
    doc1_content = next(s for s in qe_sections if s["page_start"] == 3)["content"]
    doc2_content = next(s for s in qe_sections if s["page_start"] == 40)["content"]
    assert all("doc1" in c for c in doc1_content)
    assert all("doc2" in c for c in doc2_content)


def test_truncation_is_explicit_not_silent(monkeypatch):
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(
        rag_service, "_get_thread_topics",
        lambda tid: _topics(("Quadratic Equations", 1)),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_metadata_from_db",
        lambda tid: {"num_pages": 5, "filename": "big.pdf"},
    )
    many_chunks = {1: [f"chunk {i}" for i in range(10)]}
    monkeypatch.setattr(
        "app.utils.rag_vectorstore.query_chunks_by_page_range",
        _chunks_for_range(many_chunks),
    )
    monkeypatch.setattr(rag_service, "_TEACH_TOPIC_MAX_CHUNKS", 3)

    result = rag_service.teach_topic_tool.invoke({"topic": "Quadratic Equations", "thread_id": "user_1_abc"})
    assert result["truncated"] is True
    assert result["total_chunks_retrieved"] == 3


def test_many_matched_sections_are_capped_not_dumped_whole(monkeypatch):
    """
    Regression test for a live bug: on a real 52-page document that is ENTIRELY about
    "Quadratic Equations" (a merged textbook chapter + 8 lesson-plan guides + SLO tables),
    teach_topic_tool matched 34 sections / 55 chunks in one tool result. Returning all of it
    overflowed the model's context and the agent looped until LangGraph's recursion limit
    killed the turn with a 500. Sections must be capped by relevance, with the rest reported
    explicitly (never silently dropped).
    """
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    # 20 headings, all strong matches for "Quadratic Equations" (some exact, some partial-but-above-threshold).
    monkeypatch.setattr(
        rag_service, "_get_thread_topics",
        lambda tid: _topics(*[(f"Quadratic Equations Section {i}", i) for i in range(1, 21)]),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_metadata_from_db",
        lambda tid: {"num_pages": 25, "filename": "All_merged.pdf"},
    )
    monkeypatch.setattr(
        "app.utils.rag_vectorstore.query_chunks_by_page_range",
        _chunks_for_range({i: [f"chunk on page {i}"] for i in range(1, 26)}),
    )
    monkeypatch.setattr(rag_service, "_TEACH_TOPIC_MAX_SECTIONS", 5)

    result = rag_service.teach_topic_tool.invoke({"topic": "Quadratic Equations", "thread_id": "user_1_abc"})

    assert len(result["matched_sections"]) == 5
    assert result["truncated"] is True
    assert "additional_sections_not_included" in result
    assert len(result["additional_sections_not_included"]) == 15
    # Nothing silently dropped: every matched heading is accounted for in one list or the other.
    shown = {s["heading"] for s in result["matched_sections"]}
    omitted = {s["heading"] for s in result["additional_sections_not_included"]}
    assert len(shown | omitted) == 20
    assert "message" in result


def test_registered_in_tools_list_for_llm_binding():
    assert rag_service.teach_topic_tool in rag_service.tools


def test_footer_offset_table_matches_validated_corpus():
    """CIE / Versa / Sacred qualify; Alpha (conf 0.07 / votes 3) does not."""
    cie = {}
    for i in range(11):
        cie[10 + i] = (10 + i) - 3
    for i in range(9):
        cie[200 + i] = 50 + i * 3
    cie_stats = rag_service._derive_footer_offset(cie)
    assert cie_stats["offset"] == -3
    assert cie_stats["votes"] == 11
    assert abs(cie_stats["confidence"] - 0.55) < 0.02
    assert rag_service._footer_offset_qualifies(cie_stats)

    versa = {10 + i: (10 + i) + 32 for i in range(52)}
    for i in range(4):
        versa[900 + i] = i + 1
    versa_stats = rag_service._derive_footer_offset(versa)
    assert versa_stats["offset"] == 32
    assert versa_stats["votes"] == 52
    assert versa_stats["confidence"] >= 0.5
    assert rag_service._footer_offset_qualifies(versa_stats)

    sacred = {i: i for i in range(1, 221)}
    sacred_stats = rag_service._derive_footer_offset(sacred)
    assert sacred_stats["offset"] == 0
    assert sacred_stats["votes"] == 220
    assert sacred_stats["confidence"] == 1.0
    assert rag_service._footer_offset_qualifies(sacred_stats)
    assert rag_service._logical_map_from_offset(0, 220) == {}

    alpha = {10: 12, 20: 21, 30: 40}
    for i in range(40):
        alpha[1000 + i] = 7 + (i % 17)
    alpha_stats = rag_service._derive_footer_offset(alpha)
    assert alpha_stats["votes"] <= 4 or alpha_stats["confidence"] < 0.5
    mapping, meta = rag_service._qualify_footer_logical_map(alpha, num_pages=100, thread_id="t_alpha")
    assert mapping == {}
    assert meta["page_map_unusable"] is True


def test_offset_resolution_applied_before_page_ranges(monkeypatch):
    """Printed 14/16 must become physical 11/13 before section end is computed."""
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "_page_map_is_unusable", lambda tid: False)
    monkeypatch.setattr(
        rag_service, "_resolve_requested_page",
        lambda page_requested, thread_id: (int(page_requested) - 3, "logical_page_map"),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_topics",
        lambda tid: _topics(
            ("Forces and motion", 12),
            ("Measuring length and time", 14),
            ("Density", 16),
        ),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_metadata_from_db",
        lambda tid: {"num_pages": 47, "filename": "CIE PHYSICS.pdf"},
    )
    monkeypatch.setattr(
        "app.utils.rag_vectorstore.query_chunks_by_page_range",
        _chunks_for_range({11: ["length and time body on physical page 11"]}),
    )

    result = rag_service.teach_topic_tool.invoke(
        {"topic": "Measuring length and time", "thread_id": "user_1_abc"}
    )
    section = next(s for s in result["matched_sections"] if "Measuring length" in s["heading"])
    assert section["page_start"] == 11
    assert section["page_end"] == 12
    assert section["chunks_found"] == 1
    assert "physical page 11" in section["content"][0]


def test_out_of_range_topic_returns_excerpt_status(monkeypatch):
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "_page_map_is_unusable", lambda tid: False)
    monkeypatch.setattr(
        rag_service, "_resolve_requested_page",
        lambda page_requested, thread_id: (int(page_requested) - 3, "logical_page_map"),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_topics",
        lambda tid: _topics(
            ("Measuring length and time", 14),
            ("Logic Gates 1 and 2", 238),
        ),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_metadata_from_db",
        lambda tid: {"num_pages": 47, "filename": "CIE PHYSICS.pdf"},
    )

    result = rag_service.teach_topic_tool.invoke(
        {"topic": "Logic Gates 1 and 2", "thread_id": "user_1_abc"}
    )
    assert result["matched_sections"] == []
    assert result.get("status") == "in_contents_not_in_this_upload"
    assert "excerpt" in result["message"].lower()
    assert "rag_tool" not in result["message"]
    assert "teach_topic_tool" not in result["message"]
    related = {r["heading"] for r in result.get("related_not_covered") or []}
    assert "Logic Gates 1 and 2" not in related


def test_none_page_heading_not_silently_demoted_to_related(monkeypatch):
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "_page_map_is_unusable", lambda tid: False)
    monkeypatch.setattr(
        rag_service, "_get_thread_topics",
        lambda tid: _topics(
            ("Measuring length and time", None),
            ("Density", 20),
        ),
    )
    monkeypatch.setattr(
        rag_service, "_get_thread_metadata_from_db",
        lambda tid: {"num_pages": 47, "filename": "CIE PHYSICS.pdf"},
    )
    monkeypatch.setattr(rag_service, "_try_semantic_chunks_for_query", lambda *a, **k: [])

    result = rag_service.teach_topic_tool.invoke(
        {"topic": "Measuring length and time", "thread_id": "user_1_abc"}
    )
    related = {r["heading"] for r in result.get("related_not_covered") or []}
    assert "Measuring length and time" not in related
    unresolved = {r["heading"] for r in result.get("unresolved_matches") or []}
    assert "Measuring length and time" in unresolved
    assert result.get("status") == "matched_heading_without_page"
