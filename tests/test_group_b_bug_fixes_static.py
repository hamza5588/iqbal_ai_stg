"""
Regression tests for Group B (bugs #7, #8, #25).

#8 / #25 - Saved lessons could be missing equations, or "Save" could persist
    the wrong/stale content. Root cause: both the frontend
    (currentLessonMarkdown) and backend (RAGThread.last_lesson_text) only
    captured an AI turn as the in-progress lesson when it "looked like a
    lesson" (len > 200 chars, or contained '#'/'\\n\\n'). A short but
    legitimate lesson edit (e.g. "I've added that equation") failed this
    shape check and was silently dropped, so Save persisted an earlier turn.
#7 - Saved lesson names were inconsistent because "Save Lesson" (from chat)
    ignored the title the teacher explicitly entered and instead used the
    auto-generated, 20-char-truncated chat-sidebar title (sometimes literally
    "New Conversation").

Run either via `pytest tests/` or directly via
`python tests/test_group_b_bug_fixes_static.py` (no pytest required).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_SRC = (ROOT / "templates" / "teacher_dashboard.html").read_text(encoding="utf-8")
RAG_SERVICE_SRC = (ROOT / "app" / "utils" / "rag_service.py").read_text(encoding="utf-8")

BUGGY_HEURISTIC_FRONTEND = "aiResponse.length > 200 || (aiResponse.indexOf('#')"
BUGGY_HEURISTIC_BACKEND = (
    'len(response_content) > 200 or "#" in response_content or "\\n\\n" in response_content'
)


def _save_lesson_to_my_lessons_body():
    m = re.search(
        r"async function saveLessonToMyLessons\(pdfDataOverride, lessonContentOverride\)(.*?)\n      \}\n",
        TEMPLATE_SRC,
        re.S,
    )
    assert m, "saveLessonToMyLessons() not found in teacher_dashboard.html"
    return m.group(1)


# --- Bugs #8 / #25: short lesson-editing turns were dropped ----------------

def test_frontend_lesson_markdown_heuristic_removed():
    assert BUGGY_HEURISTIC_FRONTEND not in TEMPLATE_SRC, (
        "currentLessonMarkdown must no longer be gated on response length/shape - that gate "
        "dropped short lesson edits (missing equations / stale saved content)"
    )


def test_frontend_lesson_markdown_always_synced_on_active_thread():
    assert re.search(
        r"if \(window\.currentRAGThreadId && aiResponse\) \{\s*currentLessonMarkdown = aiResponse;",
        TEMPLATE_SRC,
    ), "currentLessonMarkdown must sync on every AI turn in an active RAG thread"


def test_backend_last_lesson_text_heuristic_removed():
    assert BUGGY_HEURISTIC_BACKEND not in RAG_SERVICE_SRC


def test_backend_last_lesson_text_syncs_via_update_lesson_tool_not_free_text():
    """
    Supersedes both the original Group B assertion (write gated on
    `not thread_row.lesson_finalized`) and the Phase 3 assertion that replaced it (write gated
    on `router_intent == "lesson_modification"` inside _chat_handle_lesson_state_and_persistence).

    QA-sweep bug: trusting response_content (the model's free-text chat reply) as "the full
    lesson" did not hold in practice - a natural "add a section about X" reply containing only
    the new section silently truncated a saved lesson down to that fragment (confirmed live via
    direct DB check). It also never covered the very first lesson_generation turn, so an
    immediate "save this" right after generating a lesson failed with "no lesson content yet".

    Fix: persistence now happens exclusively via update_lesson_tool, a real bound tool (like
    finalize_lesson_tool) that takes the full lesson text as an explicit, validated argument -
    never inferred from response_content - called after generation as well as every edit. This
    locks in that _chat_handle_lesson_state_and_persistence no longer writes last_lesson_text
    at all, and that update_lesson_tool's own fragment-rejection guard exists.
    """
    assert "def update_lesson_tool(full_lesson_text: str, thread_id: str) -> str:" in RAG_SERVICE_SRC, (
        "update_lesson_tool must exist as the structured persistence path for lesson "
        "creation/edits"
    )
    assert "update_lesson_tool," in RAG_SERVICE_SRC and "\ntools = [" in RAG_SERVICE_SRC, (
        "update_lesson_tool must be bound alongside the other tools"
    )

    persistence_fn_marker = "def _chat_handle_lesson_state_and_persistence("
    idx = RAG_SERVICE_SRC.find(persistence_fn_marker)
    assert idx != -1, "_chat_handle_lesson_state_and_persistence not found"
    next_fn_idx = RAG_SERVICE_SRC.find("\ndef ", idx + len(persistence_fn_marker))
    fn_body = RAG_SERVICE_SRC[idx:next_fn_idx if next_fn_idx != -1 else idx + 4000]
    assert "thread_row.last_lesson_text = response_content" not in fn_body, (
        "_chat_handle_lesson_state_and_persistence must no longer blindly write "
        "last_lesson_text from the free-text chat reply - that is exactly the bug that "
        "truncated a saved lesson down to a single fragment. Persistence must go through "
        "update_lesson_tool's validated structured argument instead."
    )

    update_tool_marker = "def update_lesson_tool(full_lesson_text: str, thread_id: str) -> str:"
    tool_idx = RAG_SERVICE_SRC.find(update_tool_marker)
    tool_window = RAG_SERVICE_SRC[tool_idx: tool_idx + 4500]
    assert "thread_row.last_lesson_text = content" in tool_window, (
        "update_lesson_tool must be the one writing last_lesson_text"
    )
    assert "len(content) < 0.5 * len(previous)" in tool_window, (
        "update_lesson_tool must reject a reply that looks like only a fragment of the "
        "lesson rather than silently persisting it and truncating the saved lesson"
    )


# --- Bug #7: saved lesson name must use the teacher's explicit title -------

def test_save_lesson_reads_explicit_pdf_title():
    body = _save_lesson_to_my_lessons_body()
    assert "explicitTitle" in body and "pdfData.title" in body


def test_save_lesson_prefers_explicit_title_over_chat_title():
    body = _save_lesson_to_my_lessons_body()
    assert "const baseTitle = explicitTitle || getCurrentChatThreadTitleForLesson();" in body, (
        "Save must prefer the teacher's explicitly entered lesson title over the "
        "auto-generated (and 20-char-truncated) chat-sidebar title"
    )


if __name__ == "__main__":
    import sys

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
    print(f"All {len(tests)} Group B tests passed - bugs #7, #8, and #25 fixes are in place.")
    sys.exit(0)
