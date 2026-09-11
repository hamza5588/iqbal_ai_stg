"""The post-diagnostic tutor must (a) default to short, one-step-at-a-time
replies (DIL feedback: answers were too hard and too long), (b) switch to a
real-life example when the student says they don't get it instead of just
rephrasing, (c) tell the model NOT to repeat once it has already answered on
this question, (d) name the student's grade when known."""
import importlib.util
import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_p = _ROOT / "app" / "services" / "lms" / "tutor_service.py"


def _load():
    # Stub the two app modules tutor_service imports at module load so it
    # loads without a DB / sqlalchemy.
    for name in ("app", "app.services", "app.services.lms",
                 "app.services.lms.performance_service", "app.utils",
                 "app.utils.groq_rate_limit", "app.utils.llm_factory"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["app.services.lms.performance_service"].get_student_mastery = lambda *_a, **_k: []
    sys.modules["app.utils.groq_rate_limit"].invoke_with_groq_rate_limit = lambda f, **_k: f()
    sys.modules["app.utils.llm_factory"].create_llm = lambda **_k: None
    spec = importlib.util.spec_from_file_location("_tutor_under_test", _p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = _load()


def test_reply_rules_always_present():
    ctx = T.build_deficiency_context(current_question={"question_text": "2+2?"}, assist_level=1)
    assert "REPLY RULES" in ctx
    assert "AT MOST 2 short sentences" in ctx
    assert "ONE step at a time" in ctx


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


def test_assist_level_instruction_included():
    ctx = T.build_deficiency_context(assist_level=5)
    assert "Level 5" in ctx


def test_level_5_is_the_documented_exception_to_the_2_sentence_rule():
    ctx = T.build_deficiency_context(assist_level=5)
    assert "exception to the 2-sentence rule" in ctx


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
