"""The post-diagnostic tutor must (a) always carry reading-level rules,
(b) tell the model NOT to repeat once it has already answered on this
question, (c) name the student's grade when known."""
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


def test_reading_level_rules_always_present():
    ctx = T.build_deficiency_context(current_question={"question_text": "2+2?"}, assist_level=1)
    assert "READING LEVEL RULES" in ctx
    assert "Short sentences" in ctx


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
