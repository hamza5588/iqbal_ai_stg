"""Scope rules shared by the student lesson chat (lesson_qa_graph + StudentLessonService).

Lesson chat answers only from the lesson and its uploaded PDF. The general AI Tutor
(app/services/lms/tutor_service.py) is separate and is not restricted by this module.
"""
from __future__ import annotations

OUT_OF_SCOPE_MESSAGE = (
    "This question is outside the uploaded lesson material. "
    "Please ask a question related to this lesson."
)

# Distinctive fragment used to recognise the reply even if the model adds a header,
# quotes or trailing whitespace around it.
_OUT_OF_SCOPE_MARKER = "outside the uploaded lesson material"


def normalize_out_of_scope(answer: str | None) -> str:
    """Return exactly OUT_OF_SCOPE_MESSAGE when the model signalled out-of-scope,
    so no partial off-topic content leaks through alongside it."""
    text = (answer or "").strip()
    if _OUT_OF_SCOPE_MARKER in text.lower():
        return OUT_OF_SCOPE_MESSAGE
    return text
