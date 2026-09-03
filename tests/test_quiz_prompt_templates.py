"""
Regression test for a real production bug: quiz-generation prompt templates
are plain Python strings later called with `.format(keyword=value, ...)`,
which means ANY literal curly brace in the template's own instructional
text - e.g. a LaTeX example like "x^{2}" or "\\frac{num}{den}" - gets
misparsed as a format placeholder unless doubled ({{ }}). Since these
`.format()` calls pass only keyword arguments (an empty positional args
tuple), a stray numeric placeholder like "{2}" fails immediately with
"IndexError: Replacement index 2 out of range for positional args tuple" -
exactly the error a teacher hit uploading a real PDF (math content full of
exponents/fractions written as literal examples in the prompt text).

Found and fixed: _EXTRACTION_PROMPT and _LATEX_NORMALIZE_PROMPT (both in
pdf_extractor.py) and _MCQ_PROMPT (mcq_converter.py) each had at least one
unescaped literal brace. This test parses every quiz-generation prompt
template with Python's own Formatter and asserts every field found is one
of the template's real, intended keyword arguments - so a future edit that
adds an unescaped example breaks the test instead of production.
"""
import string

import pytest

from app.services.quiz.diagnostic_generator import _CONTENT_MCQ_PROMPT
from app.services.quiz.mcq_converter import _MCQ_PROMPT
from app.services.quiz.pdf_extractor import _EXTRACTION_PROMPT, _LATEX_NORMALIZE_PROMPT, _PAIRING_PROMPT
from app.services.quiz.remediation_generator import _REMEDIATION_PROMPT

TEMPLATES = {
    "_EXTRACTION_PROMPT": (_EXTRACTION_PROMPT, {"text"}),
    "_PAIRING_PROMPT": (_PAIRING_PROMPT, {"questions", "answers"}),
    "_LATEX_NORMALIZE_PROMPT": (_LATEX_NORMALIZE_PROMPT, {"questions"}),
    "_MCQ_PROMPT": (_MCQ_PROMPT, {"question", "question_latex", "answer", "answer_latex", "retry_hint"}),
    "_CONTENT_MCQ_PROMPT": (_CONTENT_MCQ_PROMPT, {"count", "topic", "content", "retry_hint"}),
    "_REMEDIATION_PROMPT": (
        _REMEDIATION_PROMPT,
        {
            "count", "topic_name", "topic_description", "score_percent",
            "difficulty", "purpose_label", "exclude_block", "retry_hint",
        },
    ),
}


@pytest.mark.parametrize("name", list(TEMPLATES.keys()))
def test_prompt_template_has_no_stray_format_fields(name):
    template, expected_fields = TEMPLATES[name]
    found = set()
    for _literal, field, _spec, _conv in string.Formatter().parse(template):
        if field is not None:
            found.add(field)
    unexpected = found - expected_fields
    assert not unexpected, (
        f"{name} has unescaped literal brace(s) parsed as format field(s) {unexpected} "
        f"- likely a LaTeX/math example like 'x^{{2}}' or '\\\\frac{{num}}{{den}}' written "
        f"without doubling the braces. Escape it as '{{{{' / '}}}}' in the template."
    )


@pytest.mark.parametrize("name", list(TEMPLATES.keys()))
def test_prompt_template_formats_without_raising(name):
    """The actual end-to-end check: .format() with only the real keyword
    args (no positional args - matches how every call site invokes these)
    must not raise, even when values themselves contain braces (e.g. a
    JSON payload or PDF text with math notation)."""
    template, expected_fields = TEMPLATES[name]
    kwargs = {f: "sample {2} text with a^{3}b^{2} braces in the VALUE" for f in expected_fields}
    template.format(**kwargs)  # must not raise
