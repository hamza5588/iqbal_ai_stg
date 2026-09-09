"""The diagnostic 'clarify' feature must never leak the answer. Tests the
leak guard (_looks_leaky) that rejects a model reply pointing at an option."""
import importlib.util
import pathlib

_p = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "lms" / "question_clarify_service.py"


def _load_guard():
    """Load just the regex + guard function without importing app.models."""
    src = _p.read_text(encoding="utf-8")
    # keep everything up to the first `def _parse_meta` (the pure part:
    # imports of stdlib re/json + _MAX_CHARS + _LEAK_RE + helpers below it
    # need _LEAK_RE only). Extract _LEAK_RE and _looks_leaky by exec of a
    # trimmed module.
    import re as _re

    ns: dict = {"re": _re}
    # _LEAK_RE assignment block
    m = _re.search(r"_LEAK_RE = re\.compile\(\n(?:.*\n)*?\)\n", src)
    exec(m.group(0), ns)
    # _looks_leaky function
    m2 = _re.search(r"def _looks_leaky\(.*?\n(?:(?: {4}.*)?\n)*", src)
    exec(m2.group(0), ns)
    return ns["_looks_leaky"]


_looks_leaky = _load_guard()

OPTIONS = [
    {"text": "a rational terminating decimal"},
    {"text": "an irrational number"},
    {"text": "the sum of a rational number and an irrational number is always irrational"},
]


def test_plain_rephrase_passes():
    good = "This question asks which description fits the number 5.1818... . A decimal that repeats forever is one type; work out which type this is."
    assert _looks_leaky(good, OPTIONS) is False


def test_stating_the_answer_is_blocked():
    assert _looks_leaky("The answer is that it is an irrational number.", OPTIONS)


def test_pointing_at_an_option_letter_is_blocked():
    assert _looks_leaky("Focus on option B when you read the choices.", OPTIONS)


def test_eliminating_options_is_blocked():
    assert _looks_leaky("You can rule out the terminating decimal choice.", OPTIONS)


def test_sharing_a_common_term_is_allowed():
    # "irrational number" also appears as an option, but a rephrase is
    # allowed to use the term the question is about.
    assert _looks_leaky("This asks you to identify what kind of number an irrational number is.", OPTIONS) is False


def test_echoing_a_full_option_statement_is_blocked():
    leak = "In other words: is the sum of a rational number and an irrational number is always irrational?"
    assert _looks_leaky(leak, OPTIONS)
