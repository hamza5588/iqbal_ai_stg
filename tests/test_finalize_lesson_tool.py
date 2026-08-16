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


# --- _parse_lesson_title_from_content: markdown-heading fallback ------------------
#
# QA-sweep Low-severity finding: lesson_title was consistently left blank in the DB despite
# the saved lesson clearly having a heading - the parser only recognized a literal
# "Lesson Title: ..." string, which the model almost never actually writes; it writes a normal
# markdown heading instead.

def test_parse_lesson_title_literal_lesson_title_string():
    assert rag_service._parse_lesson_title_from_content('Lesson Title: "Composting Basics"') == "Composting Basics"


def test_parse_lesson_title_falls_back_to_h1_heading():
    content = "# Composting Basics\n\nSome lesson content here."
    assert rag_service._parse_lesson_title_from_content(content) == "Composting Basics"


def test_parse_lesson_title_falls_back_to_h2_heading():
    content = "## Understanding the Discriminant\n\nSome content here."
    assert rag_service._parse_lesson_title_from_content(content) == "Understanding the Discriminant"


def test_parse_lesson_title_prefers_literal_string_over_heading():
    content = 'Lesson Title: "The Real Title"\n\n# A Different Heading\n\nBody text.'
    assert rag_service._parse_lesson_title_from_content(content) == "The Real Title"


def test_parse_lesson_title_empty_when_no_heading_or_literal_string():
    assert rag_service._parse_lesson_title_from_content("Just a plain paragraph, no heading.") == ""


def test_parse_lesson_title_empty_content():
    assert rag_service._parse_lesson_title_from_content("") == ""


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

    def _fake_persist(tid, content):
        persist_calls.append((tid, content))
        return True  # confirms the commit actually happened

    monkeypatch.setattr(rag_service, "_persist_finalized_lesson_static", _fake_persist)

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


def test_no_explicit_tool_call_still_persists_via_deterministic_fallback(monkeypatch):
    """
    History: Group B fix wrote response_content directly; Phase 3 kept that but re-gated it on
    router_intent; the first version of the update_lesson_tool fix REQUIRED an explicit tool
    call and stopped writing anything otherwise - but live end-to-end testing showed the model
    never calls the tool on its own (0/4 turns in a real generate/save/modify/save-again run),
    which silently regressed back to "nothing gets saved". The deterministic fallback in
    _chat_handle_lesson_state_and_persistence closes that: a plain reply with no explicit tool
    call, on a lesson_modification turn, still gets persisted - through update_lesson_tool's
    own validated path (see its guard tests), not a blind write.
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
    only fires well below the 50% threshold. Semantic check mocked True (genuinely covers the
    previous content) so this test isn't silently passing on the fail-open path."""
    original = "# Lesson\n\n" + ("Verbose original paragraph. " * 40)
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text=original, lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
    monkeypatch.setattr(rag_service, "_lesson_update_still_covers_previous", lambda *a, **k: True)

    concise_rewrite = "# Lesson\n\n" + ("Tighter paragraph. " * 32).strip()  # ~53% of original length
    result = json.loads(
        rag_service.update_lesson_tool.invoke({"full_lesson_text": concise_rewrite, "thread_id": "user_1_abc"})
    )
    assert result["success"] is True
    assert fake_db._row.last_lesson_text == concise_rewrite


# --- Semantic coverage check (catches same-length replacement, not just short fragments) ---
#
# QA-retest bug: the length-ratio guard alone missed a real failure - "add a section on the
# discriminant" returned ONLY the new discriminant section (none of the original pool-example
# lesson it was supposed to extend), and because that section alone was long enough (64% of
# the original length), it passed the 50%-length-ratio guard cleanly and silently replaced the
# saved lesson. _lesson_update_still_covers_previous is a content-aware LLM check specifically
# for this shape of failure: same-length-or-longer content that doesn't actually cover what
# came before.

def test_update_lesson_tool_rejects_same_length_content_that_drops_previous_material(monkeypatch):
    """The exact live failure: new content is long enough to pass the length-ratio guard, but
    the semantic check correctly identifies it as having dropped the previous material."""
    original = "# Setting Up a Quadratic Equation\n\n" + ("Pool example content here. " * 40)
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text=original, lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "_lesson_update_still_covers_previous", lambda *a, **k: False)

    # Long enough to pass the length-ratio guard (> 50% of original) but semantically it's a
    # different, unrelated section - exactly the live failure shape.
    replacement_section = "## Understanding the Discriminant\n\n" + ("Discriminant content here. " * 30)
    result = json.loads(
        rag_service.update_lesson_tool.invoke({"full_lesson_text": replacement_section, "thread_id": "user_1_abc"})
    )
    assert result["success"] is False
    assert "replaced or dropped" in result["reason"].lower()
    # Original lesson must survive - this is the actual data-loss bug being guarded against.
    assert fake_db._row.last_lesson_text == original
    assert fake_db.committed is False


def test_update_lesson_tool_accepts_when_semantic_check_confirms_coverage(monkeypatch):
    """When the semantic check says the new content genuinely still covers the previous
    material (e.g. a real extension), the update proceeds even if it's not a trivial
    superset-by-length."""
    original = "# Lesson\n\n" + ("Original content. " * 40)
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text=original, lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
    monkeypatch.setattr(rag_service, "_get_user_id_for_thread", lambda tid: 1)
    monkeypatch.setattr(rag_service, "_lesson_update_still_covers_previous", lambda *a, **k: True)

    extended = original + "\n\n## New Section\n\n" + ("New content. " * 20).strip()
    result = json.loads(
        rag_service.update_lesson_tool.invoke({"full_lesson_text": extended, "thread_id": "user_1_abc"})
    )
    assert result["success"] is True
    assert fake_db._row.last_lesson_text == extended


def test_update_lesson_tool_skips_semantic_check_when_no_substantial_previous_content(monkeypatch):
    """No meaningful previous content to lose (fresh lesson, previous is empty/trivial) -
    the semantic check must not even run (and definitely must not block a brand-new lesson)."""
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="", lesson_finalized=False))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
    calls = []
    monkeypatch.setattr(
        rag_service, "_lesson_update_still_covers_previous",
        lambda *a, **k: calls.append(1) or False,  # would reject if it ran at all
    )

    full_lesson = "# Brand New Lesson\n\n" + ("Content. " * 30)
    result = json.loads(
        rag_service.update_lesson_tool.invoke({"full_lesson_text": full_lesson, "thread_id": "user_1_abc"})
    )
    assert result["success"] is True
    assert calls == [], "semantic check must not run when there's no substantial previous content"


def test_lesson_update_coverage_check_fails_open_on_error(monkeypatch):
    """A validation-check outage (LLM call throws) must never block a legitimate lesson save -
    fail open (treat as covered), matching _check_if_content_is_lesson's own fail-safe
    convention elsewhere in this file (that one fails closed for a different reason: it's
    gating the higher-stakes finalize step, not an in-progress draft edit)."""
    def _raise(*a, **k):
        raise RuntimeError("LLM provider unavailable")
    monkeypatch.setattr(rag_service, "get_chat_model", _raise)

    assert rag_service._lesson_update_still_covers_previous("previous text", "new text", 1) is True


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


# --- Deterministic update_lesson_tool fallback ------------------------------------
#
# QA-sweep follow-up finding: the FIRST version of the update_lesson_tool fix (a system-prompt
# instruction telling the model to call it after generating/editing a lesson) was live-tested
# end to end (generate -> save -> modify -> save-again) and the model never called the tool
# ONCE across all 4 turns - reproducing the exact "no lesson content yet" bug this fix was
# supposed to solve. This is the same lesson already learned twice elsewhere in this codebase
# (own_answer_followup_active, meta_conversation_active both exist because a prompt-only
# instruction wasn't reliably followed either) - so _chat_handle_lesson_state_and_persistence
# now calls update_lesson_tool itself with the model's final response as a deterministic
# fallback whenever the model didn't call it, while the model calling it directly (if it does)
# is still honored and not double-processed.

def test_deterministic_fallback_calls_update_lesson_tool_when_model_did_not(monkeypatch):
    """The core fix for the live 0/4-calls finding: if the model produced real lesson content
    on a lesson_generation/lesson_modification turn but never called update_lesson_tool
    itself, the backend must call it for them."""
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="", lesson_finalized=False))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    full_lesson = "# Composting Basics\n\n" + ("Real generated lesson content. " * 20).strip()
    response = AIMessage(content=full_lesson)
    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=full_lesson,
        messages=[HumanMessage(content="create a lesson on composting")],
        last_user_msg_text="create a lesson on composting",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
        router_intent="lesson_generation",
    )
    assert result.content == full_lesson  # passthrough, not forced like finalize's response
    assert fake_db._row.last_lesson_text == full_lesson
    assert fake_db.committed is True


def test_deterministic_fallback_still_goes_through_the_fragment_guard(monkeypatch):
    """
    Critical safety property: the fallback must NOT bypass update_lesson_tool's validation -
    it calls the SAME tool, so a short confirmation-shaped reply on a lesson_modification turn
    still gets rejected instead of silently truncating a real saved lesson. This is what makes
    the fallback safe to call automatically rather than reintroducing the original blind-write
    bug under a new name.
    """
    original = "# Composting Basics\n\n" + ("Full original lesson content here. " * 40)
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text=original, lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    short_reply = "I've added 5 examples to the lesson."
    response = AIMessage(content=short_reply)
    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=short_reply,
        messages=[HumanMessage(content="add 5 examples")],
        last_user_msg_text="add 5 examples",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
        router_intent="lesson_modification",
    )
    assert result.content == short_reply  # the chat reply itself is untouched either way
    # Original lesson must survive - NOT overwritten with the short confirmation text.
    assert fake_db._row.last_lesson_text == original
    assert fake_db.committed is False


def test_deterministic_fallback_skipped_when_model_already_called_the_tool(monkeypatch):
    """If the model DID call update_lesson_tool itself this turn (a ToolMessage for it is
    present), the backend must not call it again - avoids a redundant second DB write."""
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="whatever the tool call already wrote"))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    response = AIMessage(content="I've added 5 examples to the lesson.")
    messages = [
        HumanMessage(content="add 5 examples"),
        AIMessage(content="", tool_calls=[{"name": "update_lesson_tool", "args": {}, "id": "call_1"}]),
        ToolMessage(content=json.dumps({"success": True, "reason": "Lesson draft updated."}),
                    name="update_lesson_tool", tool_call_id="call_1"),
        response,
    ]
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
        turn_scope_messages=messages,
        router_intent="lesson_modification",
    )
    assert result.content == "I've added 5 examples to the lesson."
    # Unchanged from what the (simulated) real tool call already wrote - the fallback must not
    # have run a second, redundant write with the short chat-reply text.
    assert fake_db._row.last_lesson_text == "whatever the tool call already wrote"
    assert fake_db.committed is False


def test_edit_still_persists_when_model_also_calls_finalize_same_turn(monkeypatch):
    """
    Live-discovered regression in the first version of the deterministic fallback: it was
    written as `if finalize_tool_result is not None: ... elif router_intent ==
    "lesson_modification": ...` - an elif. On a real lesson_modification turn, the model
    sometimes ALSO calls finalize_lesson_tool on its own initiative (confirmed live: router
    correctly classified the turn as lesson_modification, but the model reflexively re-saved
    after making the edit). With the elif, that finalize call short-circuited the
    update_lesson_tool fallback entirely - the edit was silently discarded, and
    finalize_lesson_tool just re-persisted the OLD pre-edit content while the forced "Lesson
    finalized and saved" message told the user the edit was saved. Both branches must run:
    the edit gets captured first, then finalize's response override applies on top of the
    now-correct DB state.
    """
    fake_db = _FakeDB(_FakeThreadRow(last_lesson_text="# Old Lesson\n\noriginal body", lesson_finalized=True))
    monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)

    full_edited_lesson = "# Old Lesson\n\noriginal body\n\n## Discriminant\n\nExplanation of the discriminant..."
    response = AIMessage(content=full_edited_lesson)
    messages = [
        HumanMessage(content="add a section on the discriminant"),
        AIMessage(content="", tool_calls=[{"name": "finalize_lesson_tool", "args": {}, "id": "call_1"}]),
        ToolMessage(
            content=json.dumps({"success": True, "reason": "Lesson re-saved with the latest content.", "already_finalized": True}),
            name="finalize_lesson_tool", tool_call_id="call_1",
        ),
        response,
    ]
    result = rag_service._chat_handle_lesson_state_and_persistence(
        response=response,
        response_content=full_edited_lesson,
        messages=messages,
        last_user_msg_text="add a section on the discriminant",
        thread_id_str="user_1_abc",
        provider="openai",
        user_llm_structured_output=None,
        config={},
        _mark_step=lambda *a, **k: None,
        turn_scope_messages=messages,
        router_intent="lesson_modification",
    )
    # The backend-authoritative finalize message still wins for what the user sees...
    assert result.content == "Lesson finalized and saved. You can download it now."
    # ...but the actual persisted content must be the NEW edited lesson, not the stale
    # pre-edit text finalize_lesson_tool would have re-saved on its own.
    assert fake_db._row.last_lesson_text == full_edited_lesson


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


# --- Phase 1 regression: false "internal error" on a save that actually succeeded ------
#
# Live bug report: router=lesson_save, prefetch_branch=specialist_handoff, finalize_lesson_tool
# executed, lesson_finalized=True in the DB, the lesson WAS actually saved - but the
# user-facing response sometimes said "An internal error occurred while trying to save the
# lesson." Root cause: get_db() returns one SQLAlchemy session shared across every tool call
# in a turn (Flask request-scoped). If an earlier operation in the same turn (e.g. the model
# calling finalize_lesson_tool twice) leaves that session in a failed-transaction state, the
# very next query on it raises immediately, even though nothing about the save itself was
# wrong. Fix: finalize_lesson_tool now rolls back and retries its own read once, and
# _persist_finalized_lesson_static now returns a real bool the caller checks instead of being
# silently trusted, so the DB's actual state is always the source of truth.

class _RollbackCapableFakeDB(_FakeDB):
    """Adds rollback() tracking and the ability to make the Nth .query() call raise, so tests
    can simulate a session left in a failed-transaction state by an earlier operation."""

    def __init__(self, row, raise_on_query_call=None):
        super().__init__(row)
        self.rollback_calls = 0
        self.query_call_count = 0
        self.raise_on_query_call = raise_on_query_call  # 1-indexed call number to raise on

    def rollback(self):
        self.rollback_calls += 1

    def query(self, model):
        self.query_call_count += 1
        if self.raise_on_query_call == self.query_call_count:
            raise RuntimeError("current transaction is aborted, commands ignored until rollback")
        return _FakeQuery(self._row)


class TestFinalizeLessonToolRetryAndFailureAccuracy:
    def test_1_successful_save_reports_success(self, monkeypatch):
        row = _FakeThreadRow(last_lesson_text="# Photosynthesis\n\nFull lesson body.", lesson_finalized=False)
        fake_db = _RollbackCapableFakeDB(row)
        monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
        monkeypatch.setattr(rag_service, "_check_if_content_is_lesson", lambda *a, **k: True)

        result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
        assert result["success"] is True
        assert result["already_finalized"] is False
        assert result["reason"] == "Lesson saved."
        assert fake_db.committed is True
        assert fake_db._row.lesson_finalized is True

    def test_2_failed_save_reports_failure_not_success(self, monkeypatch):
        """A genuine persistence failure (commit never actually happens) must never be
        reported as success - closes the inverse of the reported bug."""
        row = _FakeThreadRow(last_lesson_text="# Photosynthesis\n\nFull lesson body.", lesson_finalized=False)
        fake_db = _RollbackCapableFakeDB(row)
        monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
        monkeypatch.setattr(rag_service, "_check_if_content_is_lesson", lambda *a, **k: True)
        # Simulate _persist_finalized_lesson_static confirming the commit did NOT happen.
        monkeypatch.setattr(rag_service, "_persist_finalized_lesson_static", lambda tid, content: False)

        result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
        assert result["success"] is False
        assert "database error" in result["reason"].lower()

    def test_3_retry_after_transient_session_error_still_succeeds(self, monkeypatch):
        """The exact reported mechanism: the session's first query in this call raises (as if
        poisoned by an earlier operation in the same turn); finalize_lesson_tool must roll
        back and retry once, and the save must go through and report success - not the old
        unconditional 'internal error'."""
        row = _FakeThreadRow(last_lesson_text="# Photosynthesis\n\nFull lesson body.", lesson_finalized=False)
        fake_db = _RollbackCapableFakeDB(row, raise_on_query_call=1)  # first query raises, retry succeeds
        monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
        monkeypatch.setattr(rag_service, "_check_if_content_is_lesson", lambda *a, **k: True)

        result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
        assert result["success"] is True, f"retry must recover a transient session error, got: {result}"
        assert fake_db.rollback_calls == 1
        assert fake_db.committed is True

    def test_4_stale_error_cannot_override_a_save_that_actually_succeeded(self, monkeypatch):
        """Same mechanism as test 3, framed as the acceptance criterion: a transient error from
        earlier in the turn must never be allowed to produce a false failure response once the
        actual save completes successfully on the recovered session."""
        row = _FakeThreadRow(last_lesson_text="# Stale Error Repro\n\nLesson body.", lesson_finalized=False)
        fake_db = _RollbackCapableFakeDB(row, raise_on_query_call=1)
        monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
        monkeypatch.setattr(rag_service, "_check_if_content_is_lesson", lambda *a, **k: True)

        result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
        assert result["success"] is True
        assert "internal error" not in result["reason"].lower()
        assert fake_db._row.lesson_finalized is True, "the DB state (source of truth) must reflect the real save"

    def test_5_repeated_save_both_calls_succeed_with_correct_already_finalized_flag(self, monkeypatch):
        """Calling finalize_lesson_tool twice in a row (e.g. the model re-confirming, or the
        user saying 'save it' twice) must never error on the second call - both succeed, and
        already_finalized correctly flips False -> True."""
        row = _FakeThreadRow(last_lesson_text="# Repeated Save Lesson\n\nBody.", lesson_finalized=False)
        fake_db = _RollbackCapableFakeDB(row)
        monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
        monkeypatch.setattr(rag_service, "_check_if_content_is_lesson", lambda *a, **k: True)

        first = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
        assert first["success"] is True
        assert first["already_finalized"] is False

        second = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
        assert second["success"] is True
        assert second["already_finalized"] is True
        assert second["reason"] == "Lesson re-saved with the latest content."

    def test_6_save_after_modification_persists_the_new_content_not_stale_text(self, monkeypatch):
        """A lesson already finalized once, then edited via update_lesson_tool (which does not
        clear lesson_finalized - see its own docstring/comment), must re-validate and persist
        the NEW content when saved again, not silently skip validation and leave the old text."""
        row = _FakeThreadRow(last_lesson_text="# Original Lesson\n\nOriginal body.", lesson_finalized=True)
        fake_db = _RollbackCapableFakeDB(row)
        monkeypatch.setattr(rag_service, "get_db", lambda: fake_db)
        validation_calls = []

        def _track_validation(content, **kwargs):
            validation_calls.append(content)
            return True

        monkeypatch.setattr(rag_service, "_check_if_content_is_lesson", _track_validation)

        # Simulate update_lesson_tool having modified the draft after the original finalize.
        row.last_lesson_text = "# Original Lesson\n\nOriginal body.\n\n## New Section\n\nAdded via modification."

        result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
        assert result["success"] is True
        assert result["already_finalized"] is True
        # The re-validation must have run against the NEW content, not been skipped.
        assert validation_calls == [row.last_lesson_text] or validation_calls == [
            "# Original Lesson\n\nOriginal body.\n\n## New Section\n\nAdded via modification."
        ]
        assert fake_db._row.last_lesson_text == (
            "# Original Lesson\n\nOriginal body.\n\n## New Section\n\nAdded via modification."
        )

    def test_get_db_raising_immediately_still_returns_a_json_error_not_a_crash(self, monkeypatch):
        """Belt-and-suspenders on the pre-existing crash-safety guarantee: even get_db() itself
        failing (not just the query) must never propagate out of the tool."""
        def boom():
            raise RuntimeError("db pool exhausted")

        monkeypatch.setattr(rag_service, "get_db", boom)
        result = json.loads(rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"}))
        assert result["success"] is False
        assert "internal error" in result["reason"].lower()
