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


def test_no_tool_call_and_no_update_lesson_tool_call_does_not_persist(monkeypatch):
    """
    Formerly (Group B fix, then Phase 3): a plain conversational reply on a
    router_intent == "lesson_modification" turn was trusted as "the full lesson" and written
    directly to last_lesson_text. That trust is exactly what caused the truncation bug found
    live via QA sweep (a reply with only the new section silently deleted the rest of the
    lesson). Persistence now requires an explicit update_lesson_tool call (see that tool's own
    tests) - a plain reply with no tool call, even on a lesson_modification turn, must not
    write anything.
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

    # Unmodified - the model's normal reply passes through untouched either way.
    assert result.content == "Here's the updated lesson with a diagram section..."
    # Not persisted - only update_lesson_tool writes last_lesson_text now.
    assert fake_db._row.last_lesson_text == "old draft"
    assert fake_db.committed is False


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


# --- update_lesson_tool: structured persistence for lesson creation/edits ---------
#
# QA-sweep bug (post Phase 3): _chat_handle_lesson_state_and_persistence's
# `router_intent == "lesson_modification"` branch blindly wrote
# `thread_row.last_lesson_text = response_content`, trusting a system-prompt instruction to
# make the model always re-emit the complete lesson body in its free-text reply. Confirmed
# live that this did not hold: a natural "add a section about X" reply containing only the
# new section silently truncated a saved lesson down to that one fragment. It also never
# covered the very first lesson_generation turn at all (only "lesson_modification"), so an
# immediate "save this" right after generating a lesson was rejected as having no content to
# save. Fix: persistence now happens via an explicit update_lesson_tool call (a real bound
# tool, like finalize_lesson_tool) with the full lesson text as a validated structured
# argument, called after generation as well as every edit - never inferred from chat reply
# text. _chat_handle_lesson_state_and_persistence no longer writes last_lesson_text at all.

def test_update_lesson_tool_no_thread_id():
    result = json.loads(rag_service.update_lesson_tool.invoke({"full_lesson_text": "# Lesson", "thread_id": ""}))
    assert result["success"] is False


def test_update_lesson_tool_empty_content(monkeypatch):
    monkeypatch.setattr(rag_service, "get_db", lambda: _FakeDB(_FakeThreadRow()))
    result = json.loads(
        rag_service.update_lesson_tool.invoke({"full_lesson_text": "   ", "thread_id": "user_1_abc"})
    )
    assert result["success"] is False
    assert "no lesson content" in result["reason"].lower()


def test_update_lesson_tool_missing_thread_row(monkeypatch):
    monkeypatch.setattr(rag_service, "get_db", lambda: _FakeDB(None))
    result = json.loads(
        rag_service.update_lesson_tool.invoke({"full_lesson_text": "# Lesson\n\nbody", "thread_id": "user_1_abc"})
    )
    assert result["success"] is False
    assert "No conversation thread" in result["reason"]


def test_update_lesson_tool_persists_new_lesson_first_time(monkeypatch):
    """The lesson_generation gap: the very first save (nothing persisted yet) must succeed -
    this is exactly the "no lesson content yet" symptom from the bug report, hit immediately
    after generating a brand-new lesson."""
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text=""))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    full_lesson = "# Composting Basics\n\n" + ("Intro paragraph. " * 30).strip()
    result = json.loads(
        rag_service.update_lesson_tool.invoke({"full_lesson_text": full_lesson, "thread_id": "user_1_abc"})
    )
    assert result["success"] is True
    assert fake_db._row.last_lesson_text == full_lesson
    assert fake_db.committed is True


def test_update_lesson_tool_persists_edit_after_finalize(monkeypatch):
    """The core Phase 3 bug fix, now via the tool: previously, once lesson_finalized=True,
    nothing wrote last_lesson_text again, so a later "add 5 examples" edit was silently
    discarded."""
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="# Old Lesson\n\noriginal body", lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    full_lesson = "# Old Lesson\n\noriginal body\n\n## Examples\n\n5 new examples in detail here..."
    result = json.loads(
        rag_service.update_lesson_tool.invoke({"full_lesson_text": full_lesson, "thread_id": "user_1_abc"})
    )
    assert result["success"] is True
    assert fake_db._row.last_lesson_text == full_lesson
    # lesson_finalized is a display flag only - an edit does not touch it.
    assert fake_db._row.lesson_finalized is True


def test_update_lesson_tool_rejects_short_fragment_over_long_previous(monkeypatch):
    """
    The exact truncation bug found live: the model replies with only the NEW section
    ("add a section about X" -> just that section, not the full lesson) instead of the
    complete lesson body. Must be rejected, not silently persisted as a fragment.
    """
    original = "# Composting Basics\n\n" + ("Full original lesson content here. " * 40)
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text=original, lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    fragment_only = "## Benefits of Composting for Soil Health\n\nSome new content about soil health."
    result = json.loads(
        rag_service.update_lesson_tool.invoke({"full_lesson_text": fragment_only, "thread_id": "user_1_abc"})
    )
    assert result["success"] is False
    assert "only part of the lesson" in result["reason"].lower()
    # Must NOT have overwritten the original - this is the actual data-loss bug.
    assert fake_db._row.last_lesson_text == original
    assert fake_db.committed is False


def test_update_lesson_tool_allows_genuine_full_rewrite_even_if_shorter(monkeypatch):
    """A legitimately shorter full lesson (e.g. "make this more concise") must still be
    accepted - the guard is a fragment heuristic, not a monotonic-growth requirement, so it
    only fires well below the 50% threshold."""
    original = "# Lesson\n\n" + ("Verbose original paragraph. " * 40)
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text=original, lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    concise_rewrite = "# Lesson\n\n" + ("Tighter paragraph. " * 32).strip()  # ~53% of original length
    result = json.loads(
        rag_service.update_lesson_tool.invoke({"full_lesson_text": concise_rewrite, "thread_id": "user_1_abc"})
    )
    assert result["success"] is True
    assert fake_db._row.last_lesson_text == concise_rewrite


def test_refinalize_after_tool_edit_persists_new_content_not_stale(monkeypatch):
    """
    End-to-end symptom from the bug report: edit then "save it again" must save the NEW
    content, not silently re-persist the stale pre-edit text while claiming success.
    finalize_lesson_tool itself needs no change - it already re-reads last_lesson_text fresh
    from the DB, so once update_lesson_tool's write lands, re-finalizing picks it up for free.
    """
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="# Old Lesson\n\noriginal body", lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "_check_if_content_is_lesson", lambda *a, **k: True)

    full_lesson = "# Old Lesson\n\noriginal body\n\n## Examples\n\n5 new examples in detail here..."
    rag_service.update_lesson_tool.invoke({"full_lesson_text": full_lesson, "thread_id": "user_1_abc"})
    assert fake_db._row.last_lesson_text == full_lesson

    result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
    assert result["success"] is True
    assert result["already_finalized"] is True
    # Must reflect the edited content, not the original seed text - this is the exact
    # "lied about re-saving" symptom the bug report described.
    assert fake_db._row.last_lesson_text == full_lesson


def test_chat_handle_lesson_state_no_longer_writes_lesson_text(monkeypatch):
    """
    _chat_handle_lesson_state_and_persistence must not write last_lesson_text at all anymore
    (for ANY router_intent, including "lesson_modification") - persistence is now exclusively
    update_lesson_tool's job. This locks in the architectural change so a future edit can't
    accidentally reintroduce the free-text-trusting write path.
    """
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="original", lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    response = AIMessage(content="I've added 5 examples to the lesson.")
    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=response.content,
        messages=[HumanMessage(content="add 5 examples")],
        last_user_msg_text="add 5 examples",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
        router_intent="lesson_modification",
    )
    assert result.content == "I've added 5 examples to the lesson."
    assert fake_db._row.last_lesson_text == "original"
    assert fake_db.committed is False


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
