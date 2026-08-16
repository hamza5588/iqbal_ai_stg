"""
Tests for bug #21: PDF heading extraction silently marked itself "ready" with 0
headings when the underlying LLM calls failed for every page-batch (rate limit,
timeout, outage) - indistinguishable from "this document genuinely has no headings".

Fix: app/utils/rag_service.py's _extract_topics_with_ai now tracks how many batches
were attempted vs. failed. If every attempted batch raised, it raises instead of
returning a normal empty-topics result, so the caller (extract_and_store_headings_for_thread)
never reaches _persist_headings_for_thread and thread_row.headings_ready stays False -
which the existing on-demand recovery path in list_topics_whole_doc_tool already retries.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import pytest

rag_service = pytest.importorskip("app.utils.rag_service")

from langchain_core.documents import Document  # noqa: E402


class _AlwaysRaisesLLM:
    def invoke(self, prompt):
        raise RuntimeError("LLM provider unavailable")


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _ScriptedLLM:
    """Returns a scripted sequence of responses/exceptions, one per call."""

    def __init__(self, script):
        self._script = list(script)
        self._i = 0

    def invoke(self, prompt):
        item = self._script[self._i]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


def _page_docs(n, start_page=11):
    """Pages numbered > 10 so the TOC-detection phase is skipped (early_pages filter excludes them)."""
    return [
        Document(page_content=f"Body text for page {start_page + i} " * 20, metadata={"page": start_page + i})
        for i in range(n)
    ]


def test_all_batches_failing_raises_instead_of_returning_empty(monkeypatch):
    monkeypatch.setattr(rag_service, "get_rag_llm", lambda user_id=None: _AlwaysRaisesLLM())

    # 6 pages / batch_size 3 = 2 batches, both will fail.
    with pytest.raises(Exception):
        rag_service._extract_topics_with_ai(_page_docs(6), user_id=1, thread_id="user_1_abc")


def test_partial_batch_failure_still_returns_normally_with_found_headings(monkeypatch):
    # First batch fails, second batch succeeds with one heading.
    script = [
        RuntimeError("transient error"),
        '[{"heading": "Chapter 2: Results", "page": 14}]',
    ]
    monkeypatch.setattr(rag_service, "get_rag_llm", lambda user_id=None: _ScriptedLLM(script))

    result = rag_service._extract_topics_with_ai(_page_docs(6), user_id=1, thread_id="user_1_abc")

    assert result["topics_count"] == 1
    assert result["topics"][0]["topic"] == "Chapter 2: Results"


def test_successful_extraction_with_genuinely_no_headings_does_not_raise(monkeypatch):
    # LLM calls succeed but find nothing - this must NOT be treated as a failure.
    script = ["[]", "[]"]
    monkeypatch.setattr(rag_service, "get_rag_llm", lambda user_id=None: _ScriptedLLM(script))

    result = rag_service._extract_topics_with_ai(_page_docs(6), user_id=1, thread_id="user_1_abc")

    assert result["topics_count"] == 0
    assert result["topics"] == []


def test_delay_headings_for_load_test_ternary_is_not_dead(monkeypatch):
    """
    Regression for the copy-paste bug where both ternary branches evaluated to 'false',
    so the documented load-test delay behavior could never activate on its own.
    """
    monkeypatch.setenv("LOAD_TEST_MODE", "true")
    monkeypatch.delenv("DELAY_RAG_HEADINGS_FOR_LOAD_TEST", raising=False)
    monkeypatch.delenv("ENV", raising=False)

    import importlib
    import app.config as config_module
    importlib.reload(config_module)
    try:
        assert config_module.Config.DELAY_RAG_HEADINGS_FOR_LOAD_TEST is True
    finally:
        monkeypatch.delenv("LOAD_TEST_MODE", raising=False)
        importlib.reload(config_module)
