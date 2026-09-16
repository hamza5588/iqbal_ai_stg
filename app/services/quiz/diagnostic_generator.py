"""Generate diagnostic MCQs from PDF section content (A-318)."""
from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import ValidationError

from app.services.quiz.models import MCQBatchResult, MCQQuestion
from app.services.quiz.retry_utils import format_validation_errors, retry_on_validation_error
from app.utils.groq_rate_limit import invoke_with_groq_rate_limit
from app.utils.llm_factory import get_chat_model
from app.utils.rag_vectorstore import query_all_chunks

logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 6000

_CONTENT_MCQ_PROMPT = """Generate {count} multiple-choice diagnostic question(s) based ONLY on the educational content below.

Topic / section: {topic}
{level_line}
Content:
{content}

Rules:
- Each question must have exactly 4 unique options labeled A, B, C, D.
- Pitch every question at the target difficulty and grade level above. Do NOT
  drift easier or harder, and do NOT use skills from a higher grade.
- One clearly correct answer per question grounded in the content.
- Distractors should reflect common student mistakes.
- Option text must be the FULL choice, never only "A"/"B"/"C"/"D".
- Do NOT prefix option text with the letter (write "a repeating decimal", not "B (a repeating decimal)").
- Put LaTeX ONLY in latex fields (\\frac, \\sqrt, \\dots, \\log). Keep question_text and option text readable with normal spaces between words.
- Never copy a whole English sentence into a latex field with the spaces removed.
- Set conversion_confidence between 0 and 1 for each question.
- Set learning_concept to a short student-friendly skill name (3-8 words) for what the question tests — not the PDF heading or document title.
- Do not invent facts not supported by the content.
- Do NOT copy or lightly rephrase any question listed under "Already used (avoid repeating)".
{retry_hint}

Already used (avoid repeating):
{exclude_block}
"""


def get_section_text(
    thread_id: str,
    user_id: int,
    topic_name: str,
    page: Optional[int] = None,
    max_chars: int = _MAX_CONTEXT_CHARS,
) -> str:
    """Collect RAG chunk text for a PDF topic heading."""
    chunks = query_all_chunks(thread_id=str(thread_id), user_id=user_id)
    if not chunks:
        return ""

    selected = []
    if page is not None:
        try:
            page_int = int(page)
            selected = [c for c in chunks if int(c.get("page") or 0) == page_int]
        except (TypeError, ValueError):
            selected = []

    topic_lower = (topic_name or "").strip().lower()
    if not selected and topic_lower:
        selected = [
            c
            for c in chunks
            if topic_lower in (c.get("text") or "").lower()
            or topic_lower in (c.get("content") or "").lower()
        ]

    if not selected:
        selected = chunks[:12]

    def _chunk_key(c: dict) -> tuple:
        return (
            int(c.get("page") or 0),
            int(c.get("chunk_index") or c.get("id") or 0),
        )

    parts = []
    for c in sorted(selected, key=_chunk_key):
        text = (c.get("text") or c.get("content") or "").strip()
        if text:
            parts.append(text)

    combined = "\n\n".join(parts).strip()
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n[... truncated ...]"
    return combined


def _level_line(
    difficulty: Optional[str],
    grade_level: Optional[str],
    difficulty_ladder: Optional[List[str]] = None,
) -> str:
    parts = []
    if grade_level:
        parts.append(f"Grade level: {grade_level}")
    if difficulty_ladder:
        rungs = ", ".join(f"#{i + 1} {d}" for i, d in enumerate(difficulty_ladder))
        parts.append(
            "Return the questions ordered from easiest to hardest by this "
            f"exact ladder: {rungs}. Do not deviate from this order or these levels."
        )
    elif difficulty:
        parts.append(f"Target difficulty: {difficulty}")
    return ("\n".join(parts) + "\n") if parts else ""


def generate_mcqs_from_content(
    content: str,
    topic: str,
    count: int,
    difficulty: Optional[str] = None,
    grade_level: Optional[str] = None,
    difficulty_ladder: Optional[List[str]] = None,
    exclude_question_texts: Optional[List[str]] = None,
) -> List[MCQQuestion]:
    """Generate validated MCQs from PDF section text using structured LLM output.

    ``difficulty_ladder`` (e.g. ``["easy", "medium", "hard"]``) asks for one
    question per rung in that order; it overrides ``count``.
    """
    if not content.strip():
        raise ValueError(f"No content available for topic: {topic}")
    if difficulty_ladder:
        count = len(difficulty_ladder)
    if count < 1:
        return []
    if count > 10:
        count = 10

    llm = get_chat_model(temperature=0.4, max_tokens=4096)
    structured = llm.with_structured_output(MCQBatchResult)
    retry_hint = ""
    level_line = _level_line(difficulty, grade_level, difficulty_ladder)
    exclude = exclude_question_texts or []
    exclude_block = (
        "\n".join(f"- {t[:200]}" for t in exclude[:20])
        if exclude
        else "(none)"
    )

    def _invoke() -> MCQBatchResult:
        prompt = _CONTENT_MCQ_PROMPT.format(
            count=count,
            topic=topic,
            content=content,
            level_line=level_line,
            retry_hint=retry_hint,
            exclude_block=exclude_block,
        )
        batch: MCQBatchResult = invoke_with_groq_rate_limit(
            lambda: structured.invoke(prompt),
            description=f"diagnostic MCQ gen ({topic})",
        )
        if not batch.questions:
            raise ValidationError.from_exception_data(
                "MCQBatchResult",
                [{"type": "value_error", "loc": ("questions",), "msg": "No questions generated", "input": []}],
            )
        if len(batch.questions) > count:
            batch.questions = batch.questions[:count]
        return batch

    def _on_retry(exc: ValidationError, attempt: int) -> None:
        nonlocal retry_hint
        retry_hint = (
            f"\nPrevious attempt {attempt} failed: {format_validation_errors(exc)}. Fix these issues."
        )

    batch = retry_on_validation_error(_invoke, max_retries=2, on_retry=_on_retry)
    return batch.questions
