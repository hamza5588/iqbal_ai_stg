"""wrap_for_mathjax / to_render_string must leave every diagnostic stem or
option in a state MathJax can actually typeset - the DIL rollout reported
fractions and exponents showing as raw text.

Fixture strings are the real stored values from platform diagnostic 44.
"""
import importlib.util
import pathlib
import re

import pytest

# math_text.py is a pure-stdlib module; load it directly so the test does
# not need the whole app (sqlalchemy etc.) importable.
_mt_path = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "quiz" / "math_text.py"
_spec = importlib.util.spec_from_file_location("_math_text_under_test", _mt_path)
_mt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mt)
to_render_string = _mt.to_render_string
wrap_for_mathjax = _mt.wrap_for_mathjax
recover_latex = _mt.recover_latex
recover_fields = _mt.recover_fields


def _has_balanced_delims(s: str) -> bool:
    return s.count(r"\(") == s.count(r"\)") and s.count(r"\[") == s.count(r"\]")


@pytest.mark.parametrize(
    "raw",
    [
        r"Simplify: \frac{(a^{3}b^{2})(a^{2}b^{4})}{ab^{3}}",
        "Simplify: \n3x(2x - 5) - 2x(x - 4)",
        "Which is the correct factorization of \nx^{2} + x - 12?",
        "What is the degree of the polynomial 4x^{3}y^{2} - 7xy^{4} + 3?",
        "Which expression is equal to \n(3x - 2)2?",
    ],
)
def test_stem_math_is_delimited_for_mathjax(raw):
    out = to_render_string(raw, None, inline=False)
    assert _has_balanced_delims(out)
    # any surviving \frac / ^{ must be inside a delimiter pair
    if "\\frac" in out or "^{" in out:
        assert re.search(r"\\\(|\\\[", out), out


def test_fraction_stem_uses_display_delims():
    out = to_render_string(r"Simplify: \frac{(a^{3}b^{2})(a^{2}b^{4})}{ab^{3}}", None, inline=False)
    assert out.startswith("Simplify:")
    assert "\\[" in out and "\\]" in out
    assert "\\frac" in out


def test_prose_only_stem_is_left_alone():
    raw = "Which pair contains one rational number and one irrational number?"
    assert wrap_for_mathjax(raw, inline=False) == raw


def test_already_delimited_is_not_double_wrapped():
    raw = r"Simplify: \(\frac{a}{b}\)"
    assert wrap_for_mathjax(raw, inline=True) == raw


def test_option_exponent_gets_wrapped():
    out = to_render_string("4x^{2} - 23x", "4x^{2} - 23x", inline=True)
    assert _has_balanced_delims(out)
    assert "\\(" in out and "^{2}" in out


def test_plain_number_option_untouched():
    assert wrap_for_mathjax("0.09", inline=True) == "0.09"
    assert wrap_for_mathjax("10 days", inline=True) == "10 days"


def test_implicit_power_after_paren():
    out = to_render_string("Which expression is equal to \n(3x - 2)2?", None, inline=False)
    assert "(3x - 2)^{2}" in out
    assert re.search(r"\\\([^\\]*\(3x - 2\)\^\{2\}", out)


def test_adjacent_islands_merge():
    out = to_render_string(
        "What is the degree of the polynomial 4x^{3}y^{2} - 7xy^{4} + 3?", None, inline=False
    )
    # one wrapped span, not two back-to-back
    assert out.count(r"\(") == 1 and out.count(r"\)") == 1


def test_prose_with_no_math_never_wraps():
    raw = "the answer is a repeating decimal"
    assert wrap_for_mathjax(raw, inline=True) == raw


def test_connective_between_math_bits_is_not_one_span():
    # "x = 3 or x = -3" wrapped whole -> MathJax eats the spaces -> "3orx".
    # The word "or" must stay outside any \( \).
    out = to_render_string("x2 = 9 or x = -3", None, inline=True)
    # "or" sits outside the math span, spaces intact
    assert out == r"\(x^{2} = 9\) or x = -3"


def test_fraction_options_joined_by_or_wrap_separately():
    out = to_render_string(r"\frac{1}{2} or \frac{1}{3}", None, inline=True)
    assert out == r"\(\frac{1}{2}\) or \(\frac{1}{3}\)"


# --- LLM structured-output JSON escaping eats "\f"/"\t" before a LaTeX
# command (\frac -> a real form-feed + "rac"). Found live via E2E testing
# an AI-generated diagnostic retake variant: an option rendered as
# "rac{3}{7} imes rac{2}{5}" on screen instead of a fraction times a
# fraction. ---

def test_eaten_backslash_frac_is_restored():
    corrupted = "\x0crac{3}{7} \x09imes \x0crac{2}{5}"
    assert recover_latex(corrupted) == r"\frac{3}{7} \times \frac{2}{5}"


def test_eaten_backslash_at_the_very_start_of_the_string_is_still_restored():
    # A control-char artifact at position 0 is also what str.strip() would
    # treat as whitespace and delete first if repair ran after strip().
    corrupted = "\x0crac{5}{6} \x09imes \x0crac{3}{4}"
    out = to_render_string(corrupted, None, inline=True)
    assert out == r"\(\frac{5}{6} \times \frac{3}{4}\)"


def test_eaten_backslash_repair_is_a_noop_on_clean_text():
    clean = r"\frac{1}{2} + \times 3"
    assert recover_latex(clean) == clean


def test_eaten_backslash_repair_via_recover_fields_option_path():
    # mcq_utils.pick_display_fields calls recover_fields directly - this is
    # the Learning Chat / practice option path, not just the diagnostic one.
    text, latex = recover_fields("\x0crac{2}{5} \x09imes \x0crac{3}{4}", None)
    assert "\\frac{2}{5}" in text
    assert "\\times" in text
