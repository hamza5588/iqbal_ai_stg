"""Learning Chat tutor prompt/context regressions.

The tutor is answer-aware and adaptive: it sees the student's selected option,
keeps the correct answer private, and chooses a natural next teaching move
instead of advancing through a fixed assistance ladder.
"""
import importlib.util
import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_p = _ROOT / "app" / "services" / "lms" / "tutor_service.py"


def _load():
    # Stub the two app modules tutor_service imports at module load so it
    # loads without a DB / sqlalchemy.
    names = ("app", "app.services", "app.services.lms",
             "app.services.lms.performance_service", "app.utils",
             "app.utils.groq_rate_limit", "app.utils.llm_factory")
    previous = {name: sys.modules.get(name) for name in names}
    try:
        for name in names:
            sys.modules[name] = types.ModuleType(name)
        sys.modules["app.services.lms.performance_service"].get_student_mastery = lambda *_a, **_k: []
        sys.modules["app.utils.groq_rate_limit"].invoke_with_groq_rate_limit = lambda f, **_k: f()
        sys.modules["app.utils.groq_rate_limit"].GroqBusyError = type("GroqBusyError", (Exception,), {})
        sys.modules["app.utils.groq_rate_limit"].GroqRateLimitError = type("GroqRateLimitError", (Exception,), {})
        sys.modules["app.utils.llm_factory"].create_llm = lambda **_k: None
        # tutor_calculator is pure stdlib: load the real one into the stubbed package.
        calc_spec = importlib.util.spec_from_file_location("app.services.lms.tutor_calculator", _p.with_name("tutor_calculator.py"))
        calc = importlib.util.module_from_spec(calc_spec)
        sys.modules["app.services.lms.tutor_calculator"] = calc
        calc_spec.loader.exec_module(calc)
        sys.modules["app.services.lms"].tutor_calculator = calc
        spec = importlib.util.spec_from_file_location("_tutor_under_test", _p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


T = _load()


def test_reply_rules_always_present():
    ctx = T.build_deficiency_context(current_question={"question_text": "2+2?"}, assist_level=1)
    assert "REPLY RULES" in ctx
    assert "1-3 short sentences" in ctx
    assert "ONE idea at a time" in ctx


def test_grade_is_named_when_known():
    ctx = T.build_deficiency_context(grade_level="7", assist_level=1)
    assert "grade 7" in ctx


def test_grade_falls_back_gracefully_when_unknown():
    ctx = T.build_deficiency_context(assist_level=1)
    assert "this student's grade" in ctx


def test_no_repeat_rule_only_after_a_prior_answer():
    first = T.build_deficiency_context(assist_level=1, prior_turns=0)
    later = T.build_deficiency_context(assist_level=2, prior_turns=1)
    assert "Do NOT repeat" not in first
    assert "Do NOT repeat" in later


def test_static_assistance_ladder_is_not_in_context():
    first = T.build_deficiency_context(assist_level=1)
    later = T.build_deficiency_context(assist_level=5)
    for ctx in (first, later):
        assert "Level 1" not in ctx
        assert "Level 5" not in ctx
        assert "Full Explanation" not in ctx


def test_check_in_is_only_allowed_after_an_explanation():
    ctx = T.build_deficiency_context(assist_level=1)
    assert "If you explained an idea" in ctx
    assert "only a hint, prompt, or Socratic question" in ctx
    assert "do NOT add \"Does that make sense?\"" in ctx


def test_selected_wrong_option_is_visible_but_answer_key_is_not():
    question = {
        "question_text": "Which value makes x + 2 = 5?",
        "options": [
            {"label": "A", "text": "2"},
            {"label": "B", "text": "3"},
            {"label": "C", "text": "7"},
        ],
        "correct_option_index": 1,
        "last_selected_option_index": 2,
    }
    ctx = T.build_deficiency_context(current_question=question)
    assert "SOLVED: false" in ctx
    assert "Student's latest selected option (incorrect): C: 7" in ctx
    assert "correct_option_index" not in ctx
    assert "Correct option (use in Mode B solution)" not in ctx
    assert "Correct answer" not in ctx
    assert "B is correct" not in ctx


def test_solved_context_includes_correct_option_and_mode_b_rules():
    question = {
        "question_text": "Which value makes x + 2 = 5?",
        "options": [
            {"label": "A", "text": "2"},
            {"label": "B", "text": "3"},
            {"label": "C", "text": "7"},
        ],
        "correct_option_index": 1,
        "last_selected_option_index": 1,
    }
    ctx = T.build_deficiency_context(current_question=question, solved=True)
    assert "SOLVED: true" in ctx
    assert "SOLVED = true" in ctx or "worked solution" in ctx.lower() or "REPLY RULES (SOLVED = true)" in ctx
    assert "Student's latest selected option (correct): B: 3" in ctx
    assert "Correct option (use in Mode B solution): B: 3." in ctx
    assert "1-3 short sentences" not in ctx


# --- "I don't get it" -> real-life example, not another rephrase ---

def test_confusion_phrase_triggers_real_life_example_rule():
    for phrase in ["I don't get it", "i dont get it", "I'm confused", "still confused", "no idea"]:
        ctx = T.build_deficiency_context(assist_level=2, prior_turns=1, message=phrase)
        assert "real-life example" in ctx, phrase
        assert "Do NOT repeat your previous wording" not in ctx, phrase


def test_confusion_check_overrides_plain_no_repeat_even_on_first_turn():
    ctx = T.build_deficiency_context(assist_level=1, prior_turns=0, message="I don't understand")
    assert "real-life example" in ctx


def test_ordinary_message_does_not_trigger_confusion_rule():
    ctx = T.build_deficiency_context(assist_level=1, prior_turns=1, message="why is x squared here?")
    assert "real-life example" not in ctx
    assert "Do NOT repeat your previous wording" in ctx


def test_student_seems_confused_helper():
    assert T.student_seems_confused("I don't get it") is True
    assert T.student_seems_confused("I don't get it at all") is True
    assert T.student_seems_confused("what?") is True
    assert T.student_seems_confused("what is 2+2") is False
    assert T.student_seems_confused("can you explain the formula") is False
    assert T.student_seems_confused(None) is False


# --- same rules apply to the general student tutor, not just deficiency ---

def test_student_context_gets_reply_rules_and_confusion_handling():
    plain = T.build_student_context(1, attempt_count=1, message="how do exponents work")
    assert "REPLY RULES" in plain
    assert "Do NOT repeat your previous wording" in plain

    confused = T.build_student_context(1, attempt_count=1, message="I don't get it")
    assert "real-life example" in confused
