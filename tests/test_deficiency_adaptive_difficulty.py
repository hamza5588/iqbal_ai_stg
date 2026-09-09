"""Learning Chat practice difficulty must ramp up on correct answers and ease
back on wrong ones, starting from a rung set by the diagnostic score.

Loads only the pure helpers from deficiency_chat_service (no app imports)."""
import json
import pathlib
import re
import types

_p = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "lms" / "deficiency_chat_service.py"


def _load():
    src = _p.read_text(encoding="utf-8")
    ns: dict = {
        "json": json, "List": list, "Optional": object,
        "DeficiencyChatSession": object,
    }
    # module-level constants
    for name in ("_DIFF_LADDER", "_DIFF_RANK"):
        m = re.search(rf"^{name} = .+$", src, re.M)
        exec(m.group(0), ns)
    # pure functions (def ... up to the next top-level def/blank-blank)
    for fn in ("_start_rank_for_score", "_ladder_from", "_base_rank",
               "_current_target_rank", "_reorder_pending"):
        m = re.search(rf"\ndef {fn}\(.*?\n(?:(?:[ \t].*)?\n)*", src)
        exec(m.group(0), ns)
    return ns


NS = _load()


class FakeSession:
    def __init__(self, weak_scores, current_index):
        self.weak_topics_json = json.dumps([{"score_percent": s} for s in weak_scores])
        self.current_index = current_index


def _q(diff, answered=False, correct=None):
    return {"difficulty": diff, "answered": answered, "correct": correct}


def test_low_diagnostic_score_starts_easy():
    assert NS["_start_rank_for_score"](20.0) == 0
    assert NS["_start_rank_for_score"](58.0) == 1


def test_ladder_always_has_room_to_grow():
    assert NS["_ladder_from"](0) == ["easy", "medium", "hard"]
    assert NS["_ladder_from"](1) == ["medium", "hard"]
    assert NS["_ladder_from"](2) == ["medium", "hard"]  # never a 1-question ladder


def test_target_rank_climbs_after_correct_answers():
    session = FakeSession([30.0], current_index=2)
    questions = [_q("easy", True, True), _q("medium", True, True), _q("hard"), _q("easy")]
    # base 0, +1 +1 -> 2
    assert NS["_current_target_rank"](session, questions) == 2


def test_target_rank_drops_after_wrong_answers():
    session = FakeSession([50.0], current_index=3)
    questions = [_q("medium", True, True), _q("hard", True, False),
                 _q("hard", True, False), _q("medium")]
    # base 0, +1 -1 -1 -> clamped at 0
    assert NS["_current_target_rank"](session, questions) == 0


def test_reorder_pending_puts_target_difficulty_next():
    session = FakeSession([30.0], current_index=1)
    questions = [_q("easy", True, True), _q("easy"), _q("hard"), _q("medium")]
    # after 1 correct from base 0 -> target 1 (medium)
    NS["_reorder_pending"](session, questions)
    assert questions[0]["difficulty"] == "easy"      # answered head untouched
    assert questions[1]["difficulty"] == "medium"    # next served == target


def test_reorder_pending_is_noop_on_last_question():
    session = FakeSession([30.0], current_index=2)
    questions = [_q("easy", True, True), _q("medium", True, False), _q("hard")]
    NS["_reorder_pending"](session, questions)
    assert questions[2]["difficulty"] == "hard"
