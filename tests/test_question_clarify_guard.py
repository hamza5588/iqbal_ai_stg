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


# --- method / formula disclosure (DIL feedback: rephrase leaked the method) ---

RECT_OPTIONS = [
    {"text": "55 cm^2"}, {"text": "60 cm^2"}, {"text": "50 cm^2"}, {"text": "65 cm^2"},
]


def test_stating_the_method_is_blocked():
    leak = ("What is the area of a rectangle that is 12 cm long and 5 cm wide? "
            "The area is found by multiplying the length by the width.")
    assert _looks_leaky(leak, RECT_OPTIONS)


def test_naming_the_operation_is_blocked():
    assert _looks_leaky("To find the area, multiply 12 by 5.", RECT_OPTIONS)
    assert _looks_leaky("You just multiply the two side lengths together.", RECT_OPTIONS)
    assert _looks_leaky("The area is length times the width.", RECT_OPTIONS)


def test_pure_reword_of_the_rectangle_question_passes():
    good = ("This asks for the area of a rectangle. Area means the amount of "
            "flat space inside the shape. The rectangle is 12 cm on the long "
            "side and 5 cm on the short side. Give the area in square centimetres.")
    assert _looks_leaky(good, RECT_OPTIONS) is False
