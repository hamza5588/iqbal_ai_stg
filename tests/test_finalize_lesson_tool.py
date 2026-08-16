"""
Tests for the agentic "save this as a lesson" fix (bug #5).

Old design: a fixed list of English regex patterns tried to detect "the user wants to
save the lesson" from their message text. It missed valid phrasings (e.g. "save this
AS A lesson"), so the backend never persisted anything while the LLM - having no tool
to actually perform the save and no grounding either way - would still produce a
conversational reply that sounded like success.

New design: `finalize_lesson_tool` (app/utils/rag_service.py) is a real tool bound to
the model, exactly like rag_tool/get_page_tool. The LLM's own language understanding
decides intent (any wording, any language) and calls the tool; the tool performs the
actual DB write and returns a JSON success/reason. `_chat_handle_lesson_state_and_persistence`
then forces the user-facing reply to match the tool's real outcome - the model's own
wording is never trusted for a save/fail claim.

Phase 3 addition (see PHASE3_DESIGN.md): the in-progress draft persistence branch is
gated on the router's per-turn `router_intent` ("lesson_modification") instead of
`not thread_row.lesson_finalized`, so post-finalize edits ("add 5 examples") actually
get captured instead of being silently frozen out once a lesson is finalized.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import json

import pytest

rag_service = pytest.importorskip("app.utils.rag_service")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402


class _FakeThreadRow:
    def __init__(self, last_lesson_text="", lesson_finalized=False):
        self.last_lesson_text = last_lesson_text
        self.lesson_finalized = lesson_finalized
        self.lesson_title = ""


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self._row


class _FakeDB:
    def __init__(self, row):
        self._row = row
        self.committed = False

    def query(self, model):
        return _FakeQuery(self._row)

    def commit(self):
        self.committed = True


# --- finalize_lesson_tool: direct behavior -----------------------------------

def test_finalize_lesson_tool_no_thread_id():
    result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": ""}))
    assert result["success"] is False


def test_finalize_lesson_tool_missing_thread_row(monkeypatch):
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "get_db", lambda: _FakeDB(None))

    result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
    assert result["success"] is False
    assert "No conversation thread" in result["reason"]


def test_finalize_lesson_tool_no_content_yet(monkeypatch):
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "get_db", lambda: _FakeDB(_FakeThreadRow(last_lesson_text="")))

    result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
    assert result["success"] is False
    assert "no lesson content" in result["reason"].lower()


def test_finalize_lesson_tool_content_not_a_lesson(monkeypatch):
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(
        rag_service, "get_db", lambda: _FakeDB(_FakeThreadRow(last_lesson_text="just chit-chat"))
    )
    monkeypatch.setattr(rag_service, "_check_if_content_is_lesson", lambda *a, **k: False)
    persist_calls = []
    monkeypatch.setattr(
        rag_service, "_persist_finalized_lesson_static",
        lambda tid, content: persist_calls.append((tid, content)),
    )

    result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
    assert result["success"] is False
    assert persist_calls == [], "must not persist content that fails the is-a-lesson check"


def test_finalize_lesson_tool_success_persists_and_returns_success(monkeypatch):
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(
        rag_service, "get_db",
        lambda: _FakeDB(_FakeThreadRow(last_lesson_text="# Photosynthesis\n\nFull lesson body...")),
    )
    monkeypatch.setattr(rag_service, "_check_if_content_is_lesson", lambda *a, **k: True)
    persist_calls = []
    monkeypatch.setattr(
        rag_service, "_persist_finalized_lesson_static",
        lambda tid, content: persist_calls.append((tid, content)),
    )

    result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
    assert result["success"] is True
    assert persist_calls == [("user_1_abc", "# Photosynthesis\n\nFull lesson body...")]


def test_finalize_lesson_tool_never_crashes_on_internal_error(monkeypatch):
    """A tool that raises breaks the whole chat turn; it must always return a JSON result."""
    def boom():
        raise RuntimeError("db is down")

    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "get_db", boom)

    result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
    assert result["success"] is False


# --- _chat_handle_lesson_state_and_persistence: backend-authoritative override ----

def _make_ai_message_with_tool_call(tool_name="finalize_lesson_tool"):
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": {"thread_id": "user_1_abc"}, "id": "call_1"}],
    )


def test_response_forced_to_success_message_when_tool_succeeded(monkeypatch):
    monkeypatch.setattr(rag_service, "get_db", lambda: _FakeDB(_FakeThreadRow()))

    messages = [
        HumanMessage(content="save this as a lesson"),
        _make_ai_message_with_tool_call(),
        ToolMessage(
            content=json.dumps({"success": True, "reason": "Lesson saved.", "already_finalized": False}),
            name="finalize_lesson_tool",
            tool_call_id="call_1",
        ),
    ]
    # Simulate the model's own (untrustworthy) final reply text - it must be overridden.
    response = AIMessage(content="Sure, I think that's been handled!")

    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=response.content,
        messages=messages,
        last_user_msg_text="save this as a lesson",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
    )

    assert result.content == "Lesson finalized and saved. You can download it now."


def test_response_forced_to_failure_reason_when_tool_failed(monkeypatch):
    monkeypatch.setattr(rag_service, "get_db", lambda: _FakeDB(_FakeThreadRow()))

    failure_reason = "There is no lesson content in this conversation yet - generate a lesson first."
    messages = [
        HumanMessage(content="save this as a lesson"),
        _make_ai_message_with_tool_call(),
        ToolMessage(
            content=json.dumps({"success": False, "reason": failure_reason, "already_finalized": False}),
            name="finalize_lesson_tool",
            tool_call_id="call_1",
        ),
    ]
    # The model must not be allowed to claim success even if it tries to.
    response = AIMessage(content="Great, I've saved your lesson!")

    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=response.content,
        messages=messages,
        last_user_msg_text="save this as a lesson",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
    )

    assert result.content == failure_reason


def test_no_tool_call_falls_back_to_in_progress_persistence(monkeypatch):
    """
    Normal conversation turns with no finalize tool call still persist the draft when the
    router classified this turn as a lesson edit (Group B fix, now gated on router_intent
    instead of `not lesson_finalized` - see Phase 3 fix below). router_intent must be passed
    explicitly here: without it (default None), this turn would NOT persist under the new
    gate, since None != "lesson_modification".
    """
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="old draft"))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    messages = [
        HumanMessage(content="add a diagram section"),
    ]
    response = AIMessage(content="Here's the updated lesson with a diagram section...")

    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=response.content,
        messages=messages,
        last_user_msg_text="add a diagram section",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
        router_intent="lesson_modification",
    )

    # Unmodified - no finalize tool call happened, so the model's normal reply passes through.
    assert result.content == "Here's the updated lesson with a diagram section..."
    assert fake_db._row.last_lesson_text == "Here's the updated lesson with a diagram section..."
    assert fake_db.committed is True


def test_old_finalize_call_reordered_into_windowed_messages_does_not_leak_into_new_turn(monkeypatch):
    """
    Regression test for a live bug: after a lesson was finalized, every later unrelated
    question in the same thread got answered with "Lesson finalized and saved. You can
    download it now." instead of a real answer.

    Root cause: the trimmed/windowed `messages` actually sent to the LLM pins the CURRENT
    HumanMessage and inserts it right before the first AIMessage/ToolMessage found in the kept
    window (_trim_messages_for_token_budget) - which can be an OLDER turn's
    finalize_lesson_tool round that still fit in the token budget. That reordering makes the
    old finalize call look like it happened chronologically after the new human question, so
    the turn-scoping check ("everything after the last HumanMessage") wrongly treats it as
    belonging to the current turn.

    `turn_scope_messages` (the unwindowed, chronologically-ordered conversation) fixes this:
    turn-scoping must use it instead of the windowed+reordered `messages`.
    """
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="old draft", lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    old_finalize_call = _make_ai_message_with_tool_call()
    old_finalize_result = ToolMessage(
        content=json.dumps({"success": True, "reason": "Lesson saved.", "already_finalized": False}),
        name="finalize_lesson_tool",
        tool_call_id="call_1",
    )
    new_question = HumanMessage(content="explain why 2x, how did you get 2x and not x")

    # Windowed `messages` (what actually goes to the LLM this round): the trimming/pinning
    # logic reordered the new question BEFORE the old finalize round that still fit the budget.
    windowed_messages = [new_question, old_finalize_call, old_finalize_result]

    # turn_scope_messages: the real, unwindowed chronological order - the finalize round
    # actually happened in an earlier turn, before the new question.
    turn_scope_messages = [
        HumanMessage(content="please save this as a lesson"),
        old_finalize_call,
        old_finalize_result,
        new_question,
    ]

    response = AIMessage(content="You used 2x because the width shrinks by x on each side...")

    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=response.content,
        messages=windowed_messages,
        last_user_msg_text=new_question.content,
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
        turn_scope_messages=turn_scope_messages,
    )

    # Must answer the real question, not repeat the stale "Lesson finalized and saved" message.
    assert result.content == "You used 2x because the width shrinks by x on each side..."


# --- Phase 3: post-finalize lesson_modification persistence (PHASE3_DESIGN.md) ----

def test_modification_intent_after_finalize_updates_persisted_content(monkeypatch):
    """
    The core Phase 3 bug fix: previously, once lesson_finalized=True, this function never
    wrote last_lesson_text again, so a later "add 5 examples" edit was silently discarded.
    Gating the write on router_intent == "lesson_modification" instead of
    `not lesson_finalized` means the write now fires the same way before and after finalize.
    """
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="# Old Lesson\n\noriginal body", lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    messages = [HumanMessage(content="add 5 examples")]
    response = AIMessage(content="# Old Lesson\n\noriginal body\n\n5 new examples...")

    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=response.content,
        messages=messages,
        last_user_msg_text="add 5 examples",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
        router_intent="lesson_modification",
    )

    assert result.content == "# Old Lesson\n\noriginal body\n\n5 new examples..."
    assert fake_db._row.last_lesson_text == "# Old Lesson\n\noriginal body\n\n5 new examples..."
    # lesson_finalized is a display flag only - an edit does not touch it.
    assert fake_db._row.lesson_finalized is True
    assert fake_db.committed is True


def test_refinalize_after_edit_persists_new_content_not_stale(monkeypatch):
    """
    End-to-end symptom from the bug report: "add 5 examples" then "save it again" must save
    the NEW content, not silently re-persist the stale pre-edit text while claiming success.
    finalize_lesson_tool itself needs no change - it already re-reads last_lesson_text fresh
    from the DB, so once the modification-turn write above lands, re-finalizing picks it up
    for free.
    """
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="# Old Lesson\n\noriginal body", lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "_check_if_content_is_lesson", lambda *a, **k: True)

    # Step 1: the edit turn ("add 5 examples") persists the new content.
    edit_response = AIMessage(content="# Old Lesson\n\noriginal body\n\n5 new examples...")
    rag_service._chat_handle_lesson_state_and_persistence(
        response=edit_response,
        response_content=edit_response.content,
        messages=[HumanMessage(content="add 5 examples")],
        last_user_msg_text="add 5 examples",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
        router_intent="lesson_modification",
    )
    assert fake_db._row.last_lesson_text == "# Old Lesson\n\noriginal body\n\n5 new examples..."

    # Step 2: "save it again" re-finalizes against the SAME thread row.
    result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
    assert result["success"] is True
    assert result["already_finalized"] is True
    # Must reflect the edited content, not the original seed text - this is the exact
    # "lied about re-saving" symptom the bug report described.
    assert fake_db._row.last_lesson_text == "# Old Lesson\n\noriginal body\n\n5 new examples..."


def test_document_qa_intent_never_touches_lesson_state_post_finalize(monkeypatch):
    """meta_conversation/document_qa turns must never write lesson state, even post-finalize."""
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="# Saved Lesson", lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    # Deliberately long/heading-shaped, to prove this isn't gated by a length/shape
    # heuristic - only router_intent decides whether the write happens.
    response = AIMessage(content="# Unrelated Answer\n\n" + ("This is a long unrelated reply. " * 20))

    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=response.content,
        messages=[HumanMessage(content="what does photosynthesis mean?")],
        last_user_msg_text="what does photosynthesis mean?",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
        router_intent="document_qa",
    )

    assert result.content == response.content  # passthrough, no override
    assert fake_db._row.last_lesson_text == "# Saved Lesson"
    assert fake_db._row.lesson_finalized is True
    assert fake_db.committed is False, "must never enter the write branch for a non-modification intent"


def test_meta_conversation_intent_never_touches_lesson_state_pre_finalize(monkeypatch):
    """
    Same guarantee pre-finalize: this is the pre-finalize tightening from PHASE3_DESIGN.md
    section 3 - the old `not lesson_finalized` gate had no intent check at all, so an
    unrelated turn could previously clobber the draft before the user ever finalized.
    """
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="draft so far", lesson_finalized=False))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    response = AIMessage(content="# Looks Like A Lesson\n\n" + ("padding text. " * 20))

    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=response.content,
        messages=[HumanMessage(content="what did I ask you last question?")],
        last_user_msg_text="what did I ask you last question?",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
        router_intent="meta_conversation",
    )

    assert result.content == response.content
    assert fake_db._row.last_lesson_text == "draft so far"
    assert fake_db.committed is False
