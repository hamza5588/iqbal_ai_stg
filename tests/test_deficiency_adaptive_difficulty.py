"""Learning Chat practice difficulty must ramp up on correct answers and ease
back on wrong ones, starting from a rung set by the diagnostic score.

Loads only the pure helpers from deficiency_chat_service (no app imports)."""
import json
import pathlib
import re
import types

_p = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "lms" / "deficiency_chat_service.py"


_FAKE_HEADINGS = {"t1": {"topics": [
    {"topic": "Area and Perimeter of Rectangles"},
    {"topic": "Rational and Irrational Numbers"},
    {"topic": "Solving Linear Equations"},
]}}


def _load():
    src = _p.read_text(encoding="utf-8")
    ns: dict = {
        "json": json, "List": list, "Optional": object, "Tuple": tuple,
        "DeficiencyChatSession": object,
        "_get_thread_topics": lambda tid: _FAKE_HEADINGS.get(tid, {"topics": []}),
    }
    # grab each top-level block from its header to the next top-level
    # header (def / NAME = / @decorator) or EOF
    _stop = r"(?=\ndef |\n[A-Za-z_]+ = |\n@|\Z)"
    for name in ("_DIFF_LADDER", "_DIFF_RANK", "_SECTION_STOPWORDS"):
        m = re.search(rf"\n{name} = .*?{_stop}", src, re.S)
        exec(m.group(0), ns)
    for fn in ("_start_rank_for_score", "_ladder_from", "_base_rank",
               "_current_target_rank", "_reorder_pending", "_match_target_section"):
        m = re.search(rf"\ndef {fn}\(.*?{_stop}", src, re.S)
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


# --- skill / section relevance (DIL #1, #3) ---

def test_matching_skill_finds_its_section():
    section, thread, relevance = NS["_match_target_section"]("Area of a rectangle", ["t1"])
    assert section == "Area and Perimeter of Rectangles"
    assert relevance > 0


def test_unrelated_skill_scores_zero_relevance():
    # nothing in the PDF is about probability -> caller must generate instead
    section, thread, relevance = NS["_match_target_section"]("Probability of an event", ["t1"])
    assert relevance == 0


def test_generic_words_alone_do_not_count_as_a_match():
    # "problems" / "math" are stopwords - no real overlap with any heading
    _s, _t, relevance = NS["_match_target_section"]("Word problems in math", ["t1"])
    assert relevance == 0
