"""
Dependency-free regression tests for the Medium-priority bugs (#3, #12, #22, #23).
See tests/test_medium_bugs.py for the behavioral (mocked) versions.

Run: python tests/test_medium_bugs_static.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAG_SERVICE_SRC = (ROOT / "app" / "utils" / "rag_service.py").read_text(encoding="utf-8")
LLM_FACTORY_SRC = (ROOT / "app" / "utils" / "llm_factory.py").read_text(encoding="utf-8")


# --- #3: TOC per-topic page numbers -----------------------------------------

def test_toc_prompt_asks_for_per_topic_page():
    m = re.search(r'toc_check_prompt = f"""(.*?)"""', RAG_SERVICE_SRC, re.S)
    assert m, "toc_check_prompt not found"
    body = m.group(1)
    assert '"page": page number listed next to this topic' in body


def test_toc_parsing_no_longer_reuses_toc_page_for_every_topic():
    assert (
        'topics = [{"topic": t.strip(), "page": toc_result.get("toc_page")} ' not in RAG_SERVICE_SRC
    ), "every topic must not be assigned the same toc_page value anymore"
    assert 'name = str(t.get("topic") or t.get("heading") or "").strip()' in RAG_SERVICE_SRC
    assert "page = t.get(\"page\")" in RAG_SERVICE_SRC


# --- #12: mandatory prefetch no longer asserts relevance --------------------

def test_prefetch_no_longer_unconditionally_labels_content_relevant():
    assert (
        '"## Prefetched document evidence "\n                            "(use for your answer; you may call tools again if needed)\\n\\n"'
        not in RAG_SERVICE_SRC
    )
    assert "may or may not actually be relevant" in RAG_SERVICE_SRC
    assert "treat this as an off-topic/not-in-document question" in RAG_SERVICE_SRC


# --- #22: reasoning_effort extended to gpt-oss -------------------------------

def test_gpt_oss_reasoning_effort_branch_exists():
    assert 'elif "gpt-oss" in model_lower:' in LLM_FACTORY_SRC
    assert 'os.getenv("GROQ_GPT_OSS_REASONING_EFFORT", "medium")' in LLM_FACTORY_SRC


def test_qwen_reasoning_effort_unchanged():
    assert 'os.getenv("GROQ_REASONING_EFFORT", "default")' in LLM_FACTORY_SRC


# --- #23: short/ambiguous queries get LLM-rewritten for prefetch grounding --
# (industry-standard query rewriting, not a static English phrase list - a fixed list has
# the same generalization problem the old finalize_lesson regex had: it only ever covers
# wording someone thought to hardcode, not any phrasing/language a real user might type.)

def test_expand_query_helper_exists_and_is_llm_based():
    assert "def _expand_query_for_prefetch(text: str, user_id: Optional[int]) -> str:" in RAG_SERVICE_SRC
    m = re.search(
        r"def _expand_query_for_prefetch\(.*?\n\ndef _prune_messages",
        RAG_SERVICE_SRC, re.S,
    )
    assert m, "_expand_query_for_prefetch body not found"
    body = m.group(0)
    assert "get_rag_llm(user_id=user_id" in body, "must use an LLM call, not a static phrase list"
    assert "llm.invoke(prompt)" in body


def test_no_hardcoded_english_summary_phrase_list_remains():
    assert "_SUMMARY_REQUEST_PHRASES" not in RAG_SERVICE_SRC
    assert "def _expand_short_summary_query(text: str) -> str:" not in RAG_SERVICE_SRC


def test_rewrite_only_applies_to_short_queries():
    m = re.search(
        r"def _expand_query_for_prefetch\(.*?\n\ndef _prune_messages",
        RAG_SERVICE_SRC, re.S,
    )
    body = m.group(0)
    assert "len(t.split()) > 4" in body, (
        "must skip rewriting already-specific queries - rewriting can hurt retrieval by "
        "substituting the user's own terminology (per RAG query-rewriting literature)"
    )


def test_rewrite_falls_back_to_raw_query_on_failure():
    m = re.search(
        r"def _expand_query_for_prefetch\(.*?\n\ndef _prune_messages",
        RAG_SERVICE_SRC, re.S,
    )
    body = m.group(0)
    assert "except Exception as e:" in body
    assert "return t" in body


def test_prefetch_uses_the_rewritten_query():
    assert (
        '"query": _expand_query_for_prefetch(last_user_msg_text.strip(), pf_user_id),'
        in RAG_SERVICE_SRC
    )
    assert "pf_user_id = _get_user_id_for_thread(thread_id_str)" in RAG_SERVICE_SRC


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
    print(f"All {len(tests)} tests passed - Medium bug fixes (#3, #12, #22, #23) are in place.")
    sys.exit(0)
