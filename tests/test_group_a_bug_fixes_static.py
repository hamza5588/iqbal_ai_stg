"""
Regression tests for Group A (bugs #2 and #26).

#2 - Chat sometimes answered with a stale/previous conversation's content.
     Root cause: window.currentRAGThreadId / currentRAGConversationId were
     sticky globals that clearChatContext() never actually cleared, so
     switching/starting/loading a chat could silently resume another
     conversation's LangGraph checkpoint.
#26 - "Reset Chat" only cleared the DOM/localStorage - it never called the
      backend, so the LangGraph-checkpointed history for that thread
      survived a "reset" untouched.

These files (Flask templates + a ~6000-line RAG service module with heavy
external dependencies) can't be cheaply exercised end-to-end in every
environment this suite runs in, so this module verifies the fix at the
source level: the exact buggy pattern must be gone and the fixed pattern
must be present. Can be run either via `pytest tests/` or directly via
`python tests/test_group_a_bug_fixes_static.py` (no pytest required).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_SRC = (ROOT / "templates" / "teacher_dashboard.html").read_text(encoding="utf-8")
RAG_SERVICE_SRC = (ROOT / "app" / "utils" / "rag_service.py").read_text(encoding="utf-8")
RAG_ROUTES_SRC = (ROOT / "app" / "routes" / "rag_routes.py").read_text(encoding="utf-8")


def _clear_chat_context_body():
    m = re.search(r"function clearChatContext\(\)\s*\{(.*?)\n\s*\}", TEMPLATE_SRC, re.S)
    assert m, "clearChatContext() not found in teacher_dashboard.html"
    return m.group(1)


def _send_message_body():
    m = re.search(r"async function sendMessage\(\)(.*?)\n      \}\n", TEMPLATE_SRC, re.S)
    assert m, "sendMessage() not found in teacher_dashboard.html"
    return m.group(1)


def _reset_chat_body():
    m = re.search(r"async function resetChat\(\)(.*?)\n      \}\n", TEMPLATE_SRC, re.S)
    assert m, "resetChat() not found in teacher_dashboard.html (should now be async)"
    return m.group(1)


# --- Bug #2: stale thread/conversation id reused across chat switches ------

def test_clear_chat_context_nulls_rag_thread_id():
    body = _clear_chat_context_body()
    assert "window.currentRAGThreadId = null" in body, (
        "clearChatContext() must reset window.currentRAGThreadId - it was previously a no-op, "
        "so switching chats resumed the old chat's LangGraph thread"
    )


def test_clear_chat_context_nulls_rag_conversation_id():
    body = _clear_chat_context_body()
    assert "window.currentRAGConversationId = null" in body


def test_clear_chat_context_clears_localstorage_mirrors():
    body = _clear_chat_context_body()
    assert "teacher_currentRAGThreadId" in body
    assert "teacher_currentRAGConversationId" in body


def test_start_new_chat_still_calls_clear_chat_context():
    assert re.search(r"function startNewChat\(\).*?clearChatContext\(\)", TEMPLATE_SRC, re.S)


def test_load_chat_still_calls_clear_chat_context():
    assert re.search(r"function loadChat\(chatId\).*?clearChatContext\(\)", TEMPLATE_SRC, re.S)


def test_send_message_captures_request_chat_id():
    body = _send_message_body()
    assert "const requestChatId = currentChatId;" in body, (
        "sendMessage() must snapshot the target chat before awaiting the response, so a late "
        "answer for an abandoned chat can't render into whatever chat is open when it resolves"
    )


def test_send_message_guards_against_stale_chat_switch():
    body = _send_message_body()
    assert "staleChatSwitch" in body
    assert "currentChatId !== requestChatId" in body


# --- Bug #26: Reset Chat was a DOM-only no-op -------------------------------

def test_reset_chat_calls_backend_reset_endpoint():
    body = _reset_chat_body()
    assert "/reset'" in body and "method: 'POST'" in body, (
        "resetChat() must call the backend so the LangGraph checkpoint is actually cleared, "
        "not just the visible DOM"
    )


def test_reset_chat_nulls_rag_thread_id():
    body = _reset_chat_body()
    assert "window.currentRAGThreadId = null" in body


def test_backend_clear_history_helper_exists():
    assert "def clear_thread_conversation_history(thread_id: str)" in RAG_SERVICE_SRC


def test_backend_clear_history_uses_checkpointer_not_document_delete():
    assert "checkpointer.delete_thread(thread_id_str)" in RAG_SERVICE_SRC, (
        "must clear conversation history via the checkpointer, not the document-deleting delete_thread()"
    )


def test_backend_reset_route_registered_and_validates_ownership():
    assert "@bp.route('/thread/<thread_id>/reset', methods=['POST'])" in RAG_ROUTES_SRC
    assert re.search(
        r"def reset_thread_conversation_route\(thread_id\):.*?_validate_thread_id\(thread_id, user_id\)",
        RAG_ROUTES_SRC,
        re.S,
    ), "reset route must validate the thread belongs to the requesting user before clearing it"


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
    print(f"All {len(tests)} Group A tests passed - bugs #2 and #26 fixes are in place.")
    sys.exit(0)
