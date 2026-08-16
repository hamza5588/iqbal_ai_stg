"""
Behavioral tests for Medium-priority bugs #3, #22, #23.

#12 (mandatory prefetch mislabeling off-topic content as relevant) is covered only by
the static check in test_medium_bugs_static.py - its fix lives inside
_chat_build_system_message, a large function with many required parameters/app-context
dependencies, so a full behavioral test would be disproportionately heavy for a wording
change already verified by the static source check.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import pytest

rag_service = pytest.importorskip("app.utils.rag_service")
llm_factory = pytest.importorskip("app.utils.llm_factory")


# --- #3: TOC per-topic page numbers -----------------------------------------

class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeTocLLM:
    """Returns a TOC result where each topic has its own page number."""

    def __init__(self, response_text):
        self._response_text = response_text

    def invoke(self, prompt):
        return _FakeResponse(self._response_text)


def test_toc_extraction_assigns_distinct_per_topic_pages(monkeypatch):
    import json

    toc_json = json.dumps({
        "has_toc": True,
        "toc_page": 2,  # the page the TOC listing itself is printed on
        "topics": [
            {"topic": "Chapter 1: Introduction", "page": 5},
            {"topic": "Chapter 2: Results", "page": 12},
            {"topic": "Chapter 3: Discussion", "page": 20},
        ],
    })
    monkeypatch.setattr(rag_service, "get_rag_llm", lambda user_id=None: _FakeTocLLM(toc_json))

    from langchain_core.documents import Document

    page_docs = [
        Document(page_content="Table of Contents\n" + "x" * 150, metadata={"page": 1}),
    ]

    result = rag_service._extract_topics_with_ai(page_docs, user_id=1, thread_id="user_1_abc")

    pages = [t["page"] for t in result["topics"]]
    assert pages == [5, 12, 20], "each topic must keep its own page, not all collapse to toc_page=2"


def test_toc_extraction_falls_back_gracefully_for_plain_string_topics(monkeypatch):
    """Older/malformed LLM output (plain string list) must not crash - falls back to toc_page."""
    import json

    toc_json = json.dumps({
        "has_toc": True,
        "toc_page": 2,
        "topics": ["Chapter 1", "Chapter 2"],
    })
    monkeypatch.setattr(rag_service, "get_rag_llm", lambda user_id=None: _FakeTocLLM(toc_json))

    from langchain_core.documents import Document

    page_docs = [Document(page_content="Table of Contents\n" + "x" * 150, metadata={"page": 1})]

    result = rag_service._extract_topics_with_ai(page_docs, user_id=1, thread_id="user_1_abc")

    assert [t["topic"] for t in result["topics"]] == ["Chapter 1", "Chapter 2"]
    assert all(t["page"] == 2 for t in result["topics"])


# --- #22: reasoning_effort extended to gpt-oss -------------------------------

class _FakeChatGroq:
    last_kwargs = None

    def __init__(self, **kwargs):
        _FakeChatGroq.last_kwargs = kwargs


@pytest.fixture(autouse=True)
def _patch_chat_groq(monkeypatch):
    if hasattr(llm_factory, "ChatGroq"):
        monkeypatch.setattr(llm_factory, "ChatGroq", _FakeChatGroq)
        monkeypatch.setattr(llm_factory, "GROQ_AVAILABLE", True)
    _FakeChatGroq.last_kwargs = None
    yield


def test_gpt_oss_model_gets_a_reasoning_effort():
    llm_factory.create_llm(
        temperature=0.5, api_key="gsk-test", provider="groq", model_name="openai/gpt-oss-120b"
    )
    assert _FakeChatGroq.last_kwargs.get("reasoning_effort") == "medium"


def test_qwen_model_still_gets_its_own_reasoning_effort(monkeypatch):
    monkeypatch.delenv("GROQ_REASONING_EFFORT", raising=False)
    llm_factory.create_llm(
        temperature=0.5, api_key="gsk-test", provider="groq", model_name="qwen/qwen3-32b"
    )
    assert _FakeChatGroq.last_kwargs.get("reasoning_effort") == "default"


def test_other_groq_models_get_no_reasoning_effort():
    llm_factory.create_llm(
        temperature=0.5, api_key="gsk-test", provider="groq", model_name="llama-3.3-70b-versatile"
    )
    assert "reasoning_effort" not in _FakeChatGroq.last_kwargs


# --- #23: short/ambiguous queries get LLM-rewritten for prefetch grounding --
# (replaces a hardcoded English phrase list with a real LLM call - the same fix pattern
# used for finalize_lesson_tool, generalizing to any wording/language instead of a fixed set)

class _FakeRewriteResponse:
    def __init__(self, content):
        self.content = content


class _FakeRewriteLLM:
    def __init__(self, reply):
        self._reply = reply

    def invoke(self, prompt):
        return _FakeRewriteResponse(self._reply)


class _RaisingLLM:
    def invoke(self, prompt):
        raise RuntimeError("provider timeout")


def test_short_ambiguous_query_gets_rewritten_via_llm(monkeypatch):
    monkeypatch.setattr(
        rag_service, "get_rag_llm",
        lambda user_id=None, **kw: _FakeRewriteLLM("summarize the full document covering main topics and key points"),
    )
    result = rag_service._expand_query_for_prefetch("summarize", user_id=1)
    assert result == "summarize the full document covering main topics and key points"


def test_short_non_english_query_also_gets_rewritten(monkeypatch):
    """The whole point vs. the old static list: any language/wording can be handled."""
    monkeypatch.setattr(
        rag_service, "get_rag_llm",
        lambda user_id=None, **kw: _FakeRewriteLLM("summarize the full document covering main topics and key points"),
    )
    result = rag_service._expand_query_for_prefetch("poora document samjhao", user_id=1)
    assert result == "summarize the full document covering main topics and key points"


def test_already_specific_query_is_not_sent_to_the_llm(monkeypatch):
    """Rewriting a long, already-specific query is skipped entirely (no LLM call, no risk of degrading it)."""
    def _should_not_be_called(user_id=None, **kw):
        raise AssertionError("get_rag_llm must not be called for an already-specific query")

    monkeypatch.setattr(rag_service, "get_rag_llm", _should_not_be_called)
    result = rag_service._expand_query_for_prefetch(
        "explain the process of cellular respiration in detail", user_id=1
    )
    assert result == "explain the process of cellular respiration in detail"


def test_rewrite_failure_falls_back_to_raw_query(monkeypatch):
    monkeypatch.setattr(rag_service, "get_rag_llm", lambda user_id=None, **kw: _RaisingLLM())
    result = rag_service._expand_query_for_prefetch("summarize", user_id=1)
    assert result == "summarize"


def test_empty_llm_reply_falls_back_to_raw_query(monkeypatch):
    monkeypatch.setattr(rag_service, "get_rag_llm", lambda user_id=None, **kw: _FakeRewriteLLM(""))
    result = rag_service._expand_query_for_prefetch("overview", user_id=1)
    assert result == "overview"
