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


def test_backend_last_lesson_text_always_synced_when_not_finalized():
    assert re.search(
        r'if thread_row and not getattr\(thread_row, "lesson_finalized", False\):\s*'
        r'(?:#[^\n]*\n\s*)*thread_row\.last_lesson_text = response_content',
        RAG_SERVICE_SRC,
    ), "last_lesson_text must sync on every in-progress turn, not just ones matching a shape heuristic"


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
