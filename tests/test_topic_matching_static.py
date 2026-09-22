"""
Dependency-free assertions for the lesson topic-matching release.

Run: python tests/test_topic_matching_static.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAG_SERVICE_SRC = (ROOT / "app" / "utils" / "rag_service.py").read_text(encoding="utf-8")


def _teach_topic_body():
    m = re.search(
        r"def teach_topic_tool\(topic: str, thread_id: str\) -> dict:(.*?)\n@tool\s*\ndef count_words_in_text_tool",
        RAG_SERVICE_SRC,
        re.S,
    )
    assert m, "teach_topic_tool body not found"
    return m.group(1)


def _topic_match_score_body():
    m = re.search(
        r"def _topic_match_score\(query_norm: str, query_tokens: set, heading_text: str\) -> float:(.*?)\n_TEACH_TOPIC_MATCH_THRESHOLD",
        RAG_SERVICE_SRC,
        re.S,
    )
    assert m, "_topic_match_score body not found"
    return m.group(1)


def test_resolve_requested_page_invoked_inside_teach_topic_tool():
    body = _teach_topic_body()
    assert "_resolve_requested_page(" in body, (
        "teach_topic_tool must resolve heading pages through _resolve_requested_page "
        "before computing section boundaries"
    )
    assert "page_start" in body and "page_end" in body


def test_insufficient_body_guard_untouched():
    """
    Change #3 (empty-content semantic retry) is explicitly out of scope.
    This branch must not add the backlog retry labels, and must not rewrite any
    insufficient_body block that may exist on other branches.
    """
    body = _teach_topic_body()
    assert "no_content_for_matched_headings" not in body
    assert "page_out_of_excerpt_range" not in body
    assert 'source": "semantic_fallback"' not in body
    assert "source: \"semantic_fallback\"" not in body
    # If a prior branch added insufficient_body, this release must not strip or reshape it.
    # On feature/llm-driven-agentic-routing it is absent; do not introduce it here.
    if "insufficient_body" in RAG_SERVICE_SRC:
        assert "insufficient_body" in body
        assert "_TEACH_TOPIC_MIN_BODY_CHARS" in RAG_SERVICE_SRC


def test_topic_match_score_unchanged():
    body = _topic_match_score_body()
    assert "query_norm in heading_norm or heading_norm in query_norm" in body
    assert "len(overlap) / max(len(query_tokens), 1)" in body
    assert "max(len(query_tokens), len(heading_tokens)" not in body
    assert "max(len(heading_tokens)" not in body


def test_user_facing_messages_do_not_leak_tool_names():
    body = _teach_topic_body()
    assert "fall back to rag_tool" not in body
    assert "Consider using rag_tool" not in body
    assert "in_contents_not_in_this_upload" in body
    assert "headings_building" in body
    assert "_TEACH_TOPIC_GK_OFFER" in body


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_")]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
    print()
    if failed:
        print(f"{len(failed)}/{len(tests)} test(s) FAILED")
        sys.exit(1)
    print(f"All {len(tests)} tests passed.")
    sys.exit(0)
