"""
Regression tests for _chat_limit_messages_for_llm (app/utils/rag_service.py).

Production bug found via live QA sweep (HTTP-level, real deployed server): in a growing
multi-turn conversation, once the window needed trimming, the pinned "latest human message"
was inserted "before the first AIMessage/ToolMessage found in the kept window" - which, for a
normal back-and-forth conversation, lands near the START of the kept window, not the end. The
LLM then received a scrambled transcript like [..., Q2, Q5, A2, Q3, A3, Q4, A4] instead of the
correct [..., Q2, A2, Q3, A3, Q4, A4, Q5], and answered A4's stale topic (the actual last
message in its view) instead of Q5, the user's real current question - with the router, tool
selection, and DB persistence all unaffected and correct for the same turn. Confirmed
reproducible over the real HTTP API on the live server, not reproducible via chatbot.invoke()
alone with the trimming disabled (short conversations never trigger this window).

Fix: the pinned latest human message must always be appended at the END of the kept window,
since it is - by construction (the last HumanMessage found scanning backward through the full
conversation) - chronologically after every other message that could possibly be included.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import pytest

rag_service = pytest.importorskip("app.utils.rag_service")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

SYS = SystemMessage(content="system prompt")


def _limit(conversation_messages, num_messages):
    return rag_service._chat_limit_messages_for_llm(SYS, conversation_messages, num_messages)


def _texts(messages):
    return [getattr(m, "content", None) for m in messages]


class TestNoTrimmingNeeded:
    def test_returns_everything_unchanged_when_under_budget(self):
        msgs = [HumanMessage(content="Q1"), AIMessage(content="A1")]
        result = _limit(msgs, num_messages=7)
        assert result == [SYS, *msgs]


class TestPinnedHumanMessageOrdering:
    """The exact production bug: the newest human message must land at the END of the
    trimmed window, not be spliced in near the start."""

    def test_five_turn_conversation_trimmed_to_seven_keeps_latest_question_last(self):
        # Reproduces the exact live scenario: 4 completed Q/A turns (8 messages) plus a 5th,
        # unanswered question (9 total) - the same shape as "Q1..Q4/A1..A4, then Q5" that
        # broke live. num_messages=7 matches _chat_invoke_llm_with_retry's real
        # initial_max_messages default for a normal (non-short-mode) turn.
        msgs = [
            HumanMessage(content="Q1"), AIMessage(content="A1"),
            HumanMessage(content="Q2"), AIMessage(content="A2"),
            HumanMessage(content="Q3"), AIMessage(content="A3"),
            HumanMessage(content="Q4"), AIMessage(content="A4"),
            HumanMessage(content="Q5"),
        ]
        result = _limit(msgs, num_messages=7)
        conv_only = result[1:]  # drop the system message

        # The critical assertion: Q5 (the real current question) must be the LAST message the
        # LLM sees, not buried in the middle right before an unrelated older answer.
        assert conv_only[-1].content == "Q5", (
            f"latest human message must be last, got order: {_texts(conv_only)}"
        )
        # And it must not be immediately followed by a stale answer that isn't its own -
        # the exact scrambled shape that broke live ([..., Q2, Q5, A2, ...]).
        assert "Q5" not in _texts(conv_only)[:-1]

    def test_every_kept_qa_pair_stays_correctly_adjacent(self):
        """General invariant, not just 'Q5 is last': every Ai must immediately follow its own
        Qi in the trimmed output - no answer should end up separated from or misattributed to
        a different question."""
        msgs = [
            HumanMessage(content="Q1"), AIMessage(content="A1"),
            HumanMessage(content="Q2"), AIMessage(content="A2"),
            HumanMessage(content="Q3"), AIMessage(content="A3"),
            HumanMessage(content="Q4"), AIMessage(content="A4"),
            HumanMessage(content="Q5"),
        ]
        result = _limit(msgs, num_messages=7)
        conv_only = result[1:]
        texts = _texts(conv_only)

        for i, t in enumerate(texts):
            if t and t.startswith("A"):
                q_expected = "Q" + t[1:]
                assert i > 0 and texts[i - 1] == q_expected, (
                    f"{t} is not immediately preceded by {q_expected}; order was {texts}"
                )

    def test_relative_chronological_order_of_kept_older_messages_is_preserved(self):
        """The older, already-answered turns that survive trimming must keep their own
        original relative order - only the pinned latest human message's position changes."""
        msgs = [
            HumanMessage(content="Q1"), AIMessage(content="A1"),
            HumanMessage(content="Q2"), AIMessage(content="A2"),
            HumanMessage(content="Q3"), AIMessage(content="A3"),
            HumanMessage(content="Q4"), AIMessage(content="A4"),
            HumanMessage(content="Q5"),
        ]
        result = _limit(msgs, num_messages=7)
        conv_only = result[1:]
        texts = [t for t in _texts(conv_only) if t != "Q5"]
        # Whatever subset of the older turns survived, it must appear in the same relative
        # order as the original conversation.
        original_order = _texts(msgs)[:-1]  # everything except Q5
        filtered_original = [t for t in original_order if t in texts]
        assert texts == filtered_original

    def test_two_turn_conversation_needs_no_reordering_when_within_budget(self):
        """Sanity check: when the whole conversation already fits, nothing should be
        reordered at all (covered by TestNoTrimmingNeeded too, kept here for contrast with
        the trimming case above)."""
        msgs = [HumanMessage(content="Q1"), AIMessage(content="A1"), HumanMessage(content="Q2")]
        result = _limit(msgs, num_messages=7)
        assert _texts(result[1:]) == ["Q1", "A1", "Q2"]


class TestToolCallSequencesStayIntact:
    def test_tool_call_sequence_preserved_and_latest_human_still_lands_last(self):
        tool_call = AIMessage(
            content="", tool_calls=[{"name": "rag_tool", "args": {}, "id": "call_1"}]
        )
        tool_result = ToolMessage(content="tool output", name="rag_tool", tool_call_id="call_1")
        msgs = [
            HumanMessage(content="Q1"), AIMessage(content="A1"),
            HumanMessage(content="Q2"), tool_call, tool_result, AIMessage(content="A2"),
            HumanMessage(content="Q3"), AIMessage(content="A3"),
            HumanMessage(content="Q4"),
        ]
        result = _limit(msgs, num_messages=7)
        conv_only = result[1:]

        assert conv_only[-1].content == "Q4"
        # The tool call and its result must still be adjacent and in the right order if kept.
        if tool_call in conv_only:
            tc_idx = conv_only.index(tool_call)
            assert conv_only[tc_idx + 1] is tool_result

    def test_incomplete_tool_sequence_is_dropped_not_split(self):
        """A tool-call message whose result didn't make it into the window must not be kept
        alone (would produce an invalid message sequence for the LLM API)."""
        tool_call = AIMessage(
            content="", tool_calls=[{"name": "rag_tool", "args": {}, "id": "call_1"}]
        )
        tool_result = ToolMessage(content="tool output", name="rag_tool", tool_call_id="call_1")
        msgs = [tool_call, tool_result] + [
            HumanMessage(content=f"Q{i}") if i % 2 else AIMessage(content=f"A{i}")
            for i in range(1, 9)
        ] + [HumanMessage(content="Q_latest")]
        result = _limit(msgs, num_messages=3)
        conv_only = result[1:]
        # If the tool_call made it in, its matching result must too (never split).
        if tool_call in conv_only:
            assert tool_result in conv_only
        assert conv_only[-1].content == "Q_latest"


class TestBudgetEdgeCases:
    def test_tight_budget_of_one_still_keeps_latest_human(self):
        msgs = [
            HumanMessage(content="Q1"), AIMessage(content="A1"),
            HumanMessage(content="Q2"), AIMessage(content="A2"),
            HumanMessage(content="Q3"),
        ]
        result = _limit(msgs, num_messages=1)
        conv_only = result[1:]
        assert conv_only[-1].content == "Q3"

    def test_no_human_message_at_all_does_not_crash(self):
        msgs = [AIMessage(content="A1"), AIMessage(content="A2"), AIMessage(content="A3")]
        result = _limit(msgs, num_messages=1)
        assert result[0] is SYS
