"""
Regression test for _trim_messages_for_token_budget (app/utils/rag_service.py).

Live bug: when the most recent message is a large ToolMessage (e.g. teach_topic_tool's
multi-section result), the trimmer used to force-keep it as "most recent" while dropping
its owning AIMessage(tool_calls=[...]) for being over budget, producing an orphaned tool
message. OpenAI's API then rejected the whole request with 400 "messages with role 'tool'
must be a response to a preceding message with 'tool_calls'" - on every attempt of the
retry-with-fewer-messages loop, until the ToolMessage itself finally got dropped too, at
which point the model lost all memory of having called the tool and re-issued it next
round. Reproduced live: "Create a lecture on Quadratic Equations" called teach_topic_tool
15 times in a row before the per-turn round cap forced a final answer.

Fix: an AIMessage(tool_calls=[...]) and its following ToolMessage(s) are now trimmed as
one atomic unit - kept or dropped together, never split.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import pytest

rag_service = pytest.importorskip("app.utils.rag_service")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402


def _tool_round(tool_content, tool_call_id="call_1", tool_name="teach_topic_tool"):
    ai = AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": {}, "id": tool_call_id}],
    )
    tool = ToolMessage(content=tool_content, name=tool_name, tool_call_id=tool_call_id)
    return [ai, tool]


def test_large_trailing_tool_message_keeps_or_drops_its_ai_message_together():
    system = SystemMessage(content="system prompt")
    human = HumanMessage(content="Create a lecture on Quadratic Equations.")
    huge_tool_round = _tool_round("X" * 20000)  # far larger than any reasonable token budget

    messages = [system, human, *huge_tool_round]
    trimmed = rag_service._trim_messages_for_token_budget(messages, max_input_tokens=500)

    tool_messages = [m for m in trimmed if isinstance(m, ToolMessage)]
    ai_tool_call_messages = [
        m for m in trimmed if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
    ]
    # Never split: a ToolMessage must never appear without its owning AIMessage(tool_calls).
    assert len(tool_messages) == len(ai_tool_call_messages)
    if tool_messages:
        tool_call_ids = {tc["id"] for m in ai_tool_call_messages for tc in m.tool_calls}
        assert all(tm.tool_call_id in tool_call_ids for tm in tool_messages)


def test_pinned_human_message_survives_trimming():
    system = SystemMessage(content="system prompt")
    human = HumanMessage(content="Create a lecture on Quadratic Equations.")
    huge_tool_round = _tool_round("Y" * 20000)

    messages = [system, human, *huge_tool_round]
    trimmed = rag_service._trim_messages_for_token_budget(messages, max_input_tokens=500)

    assert any(isinstance(m, HumanMessage) and m.content == human.content for m in trimmed)


def test_small_messages_all_survive_generous_budget():
    system = SystemMessage(content="system prompt")
    human = HumanMessage(content="What is a quadratic equation?")
    small_tool_round = _tool_round("A short tool result.")

    messages = [system, human, *small_tool_round]
    trimmed = rag_service._trim_messages_for_token_budget(messages, max_input_tokens=4200)

    assert trimmed == messages


def test_pinned_human_reinserted_after_older_turns_tool_round_not_before():
    """
    Regression test for a live bug that persisted even after the finalize-lesson-leak fix
    (turn_scope_messages): the pinned "most recent HumanMessage" used to be inserted
    unconditionally right before the first surviving AIMessage/ToolMessage unit, regardless of
    whether that unit was actually from an OLDER turn. That made an older
    finalize_lesson_tool exchange look like it chronologically happened AFTER the new
    question in what the model itself is shown - so the model would genuinely believe it had
    just finished that tool call and continue accordingly (repeating "Lesson finalized and
    saved") even though it never called any tool for the new turn. Reproduced live: asking an
    unrelated follow-up after finalizing a lesson kept getting the stale finalize message back,
    with server logs confirming finalize_lesson_tool was NOT called again that turn - so the
    bug was in what the model saw, not just the post-hoc override logic.
    """
    system = SystemMessage(content="system prompt " * 5)
    old_human = HumanMessage(content="please save this as a lesson")
    old_ai_call = AIMessage(
        content="", tool_calls=[{"name": "finalize_lesson_tool", "args": {}, "id": "call_1"}]
    )
    old_tool_result = ToolMessage(
        content='{"success": true, "reason": "Lesson saved.", "already_finalized": false}',
        name="finalize_lesson_tool", tool_call_id="call_1",
    )
    new_human = HumanMessage(content="explain why 2x, how did you get 2x and not x")

    messages = [system, old_human, old_ai_call, old_tool_result, new_human]
    # Small budget so the pinning/reinsertion path actually engages.
    trimmed = rag_service._trim_messages_for_token_budget(messages, max_input_tokens=200)

    assert new_human in trimmed
    idx_new_human = trimmed.index(new_human)
    if old_tool_result in trimmed:
        idx_old_tool_result = trimmed.index(old_tool_result)
        assert idx_new_human > idx_old_tool_result, (
            "the new human message must come AFTER the older turn's tool exchange, "
            "not before it - otherwise the model sees itself as having just finished "
            "that old tool call"
        )


def test_pinned_human_stays_before_same_turns_later_tool_round():
    """
    The fix above must not break the original, legitimate case: a multi-round tool-calling
    turn where the model already called a tool once THIS turn and is now on a later round -
    that tool exchange genuinely happened after the human question and must stay after it.
    """
    system = SystemMessage(content="sys")
    current_human = HumanMessage(content="Create a lecture on Quadratic Equations.")
    round1_ai_call = AIMessage(
        content="", tool_calls=[{"name": "teach_topic_tool", "args": {}, "id": "call_1"}]
    )
    round1_tool_result = ToolMessage(
        content="some lecture content", name="teach_topic_tool", tool_call_id="call_1"
    )

    messages = [system, current_human, round1_ai_call, round1_tool_result]
    trimmed = rag_service._trim_messages_for_token_budget(messages, max_input_tokens=2000)

    assert current_human in trimmed
    assert round1_tool_result in trimmed
    assert trimmed.index(current_human) < trimmed.index(round1_tool_result)
