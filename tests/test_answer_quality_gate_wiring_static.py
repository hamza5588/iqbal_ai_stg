"""
Dependency-free regression tests locking in the generalized answer-quality gate's wiring:
the old lecture-only failsafe (_lecture_failsafe_eval_and_maybe_regenerate,
LectureFailsafeEvalResult, RAG_LECTURE_FAILSAFE_*, gated on is_lesson_creation_turn alone,
default disabled) was widened to router_intent-based gating (_answer_quality_gate_eval_and_
maybe_regenerate, AnswerQualityEvalResult, RAG_ANSWER_QUALITY_GATE_*, default enabled), with
a cheap heuristic pre-filter for non-lesson intents. See PHASE2_DESIGN.md for the full design.

Run: python tests/test_answer_quality_gate_wiring_static.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAG_SERVICE_SRC = (ROOT / "app" / "utils" / "rag_service.py").read_text(encoding="utf-8")


def _gate_function_body() -> str:
    m = re.search(
        r"def _answer_quality_gate_eval_and_maybe_regenerate\(.*?\n(?=\ndef |\Z)",
        RAG_SERVICE_SRC, re.S,
    )
    assert m, "_answer_quality_gate_eval_and_maybe_regenerate not found"
    return m.group(0)


# --- Renames: old lecture-only names must be gone from the executable code -------------

def test_old_function_name_is_gone():
    assert "def _lecture_failsafe_eval_and_maybe_regenerate(" not in RAG_SERVICE_SRC


def test_old_result_model_is_gone():
    assert "class LectureFailsafeEvalResult(" not in RAG_SERVICE_SRC


def test_new_function_and_result_model_exist():
    assert "def _answer_quality_gate_eval_and_maybe_regenerate(" in RAG_SERVICE_SRC
    assert "class AnswerQualityEvalResult(" in RAG_SERVICE_SRC


def test_call_site_uses_new_function_name():
    assert "_answer_quality_gate_eval_and_maybe_regenerate(" in RAG_SERVICE_SRC
    # The call site must not still reference the old name anywhere.
    assert "_lecture_failsafe_eval_and_maybe_regenerate(" not in RAG_SERVICE_SRC


# --- Gate condition: router_intent-based, not just is_lesson_creation_turn -------------

def test_gate_checks_qualifying_intents_not_just_lesson_flag():
    body = _gate_function_body()
    assert "_answer_quality_gate_qualifying_intents()" in body
    assert "router_intent not in" in body
    # The old sole gate ("if not is_lesson_creation_turn: return") must not be the only
    # entry condition anymore.
    assert "if not is_lesson_creation_turn:\n        return response_content, response" not in body


def test_default_qualifying_intents_include_own_answer_followup():
    m = re.search(r"_ANSWER_QUALITY_GATE_DEFAULT_INTENTS = \((.*?)\)\n", RAG_SERVICE_SRC, re.S)
    assert m, "_ANSWER_QUALITY_GATE_DEFAULT_INTENTS not found"
    defaults = m.group(1)
    for intent in (
        "lesson_generation", "document_qa", "general_knowledge_qa", "lesson_qa",
        "own_answer_followup",
    ):
        assert f'"{intent}"' in defaults, f"{intent} missing from default intent set"
    # lesson_save and lesson_modification were explicitly excluded from the default set.
    assert '"lesson_save"' not in defaults
    assert '"lesson_modification"' not in defaults


def test_meta_conversation_active_short_circuits_the_gate():
    body = _gate_function_body()
    assert "if meta_conversation_active:" in body
    # Must appear before the heuristic/eval-loop machinery, i.e. near the top of the function.
    meta_idx = body.index("if meta_conversation_active:")
    eval_idx = body.index("eval_llm = user_llm.with_structured_output(AnswerQualityEvalResult)")
    assert meta_idx < eval_idx


def test_lesson_mode_bypasses_the_heuristic_prefilter():
    """Regression guard for the design's central decision: lesson-generation turns must
    keep the ORIGINAL unconditional-eval behavior (no heuristic pre-filter), because
    ungrounded lecture claims read as long well-formed prose, not short filler."""
    body = _gate_function_body()
    assert "if not is_lesson_mode:" in body
    assert "_quality_gate_should_escalate(response_content, prefetch_evidence_for_eval)" in body
    # The heuristic call must be inside the "not is_lesson_mode" branch, not called
    # unconditionally for every intent.
    guard_idx = body.index("if not is_lesson_mode:")
    escalate_idx = body.index("_quality_gate_should_escalate(response_content, prefetch_evidence_for_eval)")
    assert guard_idx < escalate_idx


def test_is_lesson_mode_derived_from_router_intent_or_legacy_flag():
    body = _gate_function_body()
    assert 'is_lesson_mode = is_lesson_creation_turn or router_intent == "lesson_generation"' in body


# --- Env vars: renamed, default flipped to enabled, old names kept as fallback --------

def test_new_enabled_env_var_defaults_to_true():
    assert '"RAG_ANSWER_QUALITY_GATE_ENABLED"' in RAG_SERVICE_SRC
    m = re.search(r"def _answer_quality_gate_enabled\(\).*?\n(?=\ndef )", RAG_SERVICE_SRC, re.S)
    assert m, "_answer_quality_gate_enabled not found"
    body = m.group(0)
    assert "return True  # new default: on" in body


def test_old_env_var_names_still_read_as_deprecated_fallback():
    """A prod/staging env that already pins RAG_LECTURE_FAILSAFE_ENABLED=false must not
    silently flip on the moment this ships."""
    assert 'os.getenv("RAG_LECTURE_FAILSAFE_ENABLED")' in RAG_SERVICE_SRC
    assert "deprecated" in RAG_SERVICE_SRC.lower()
    assert 'os.getenv("RAG_LECTURE_FAILSAFE_MAX_ROUNDS", "4")' in RAG_SERVICE_SRC
    assert 'os.getenv("RAG_LECTURE_FAILSAFE_IN_LOAD_TEST", "false")' in RAG_SERVICE_SRC


def test_new_max_rounds_and_load_test_env_vars_exist():
    assert '"RAG_ANSWER_QUALITY_GATE_MAX_ROUNDS"' in RAG_SERVICE_SRC
    assert '"RAG_ANSWER_QUALITY_GATE_IN_LOAD_TEST"' in RAG_SERVICE_SRC


def test_intents_env_var_is_configurable_with_fixed_default():
    """Ops must be able to change the qualifying-intent set without a code change (e.g. to
    drop lesson_generation later if unconditional-eval cost becomes a concern)."""
    m = re.search(r"def _answer_quality_gate_qualifying_intents\(\).*?\n(?=\ndef )", RAG_SERVICE_SRC, re.S)
    assert m, "_answer_quality_gate_qualifying_intents not found"
    body = m.group(0)
    assert 'os.getenv(\n' in body or "os.getenv(" in body
    assert '"RAG_ANSWER_QUALITY_GATE_INTENTS"' in body
    assert '",".join(_ANSWER_QUALITY_GATE_DEFAULT_INTENTS)' in body


def test_filler_max_chars_env_var_exists():
    assert '"RAG_ANSWER_QUALITY_FILLER_MAX_CHARS"' in RAG_SERVICE_SRC


# --- Heuristic pre-filter: must exclude the legitimate fallback wording ---------------

def test_heuristic_excludes_legitimate_fallback_strings():
    m = re.search(r"_LEGITIMATE_FALLBACK_PATTERNS: List\[re\.Pattern\] = \[(.*?)\]\n", RAG_SERVICE_SRC, re.S)
    assert m, "_LEGITIMATE_FALLBACK_PATTERNS not found"
    body = m.group(1)
    assert "not present in the document" in body
    assert "irrelevant question" in body


def test_filler_patterns_cover_the_live_bug_phrase():
    m = re.search(r"_FILLER_PATTERNS: List\[re\.Pattern\] = \[(.*?)\]\n", RAG_SERVICE_SRC, re.S)
    assert m, "_FILLER_PATTERNS not found"
    body = m.group(1)
    assert "feel free to ask" in body


def test_filler_patterns_cover_the_own_answer_followup_live_bug_phrase():
    """The own_answer_followup staging bug's actual filler text ("the lesson has been
    saved") is a different shape than the "feel free to ask" conversational filler -
    must be covered by its own pattern, not assumed to be caught by the others."""
    m = re.search(r"_FILLER_PATTERNS: List\[re\.Pattern\] = \[(.*?)\]\n", RAG_SERVICE_SRC, re.S)
    assert m, "_FILLER_PATTERNS not found"
    body = m.group(1)
    assert "finalized and saved" in body


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_")]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
    print()
    if failed:
        print(f"{len(failed)}/{len(tests)} test(s) FAILED")
        sys.exit(1)
    print(f"All {len(tests)} tests passed.")
    sys.exit(0)
