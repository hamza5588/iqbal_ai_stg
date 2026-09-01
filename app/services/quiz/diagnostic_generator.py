"""Generate diagnostic MCQs from PDF section content (A-318)."""
from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import ValidationError

from app.services.quiz.models import MCQBatchResult, MCQQuestion
from app.services.quiz.retry_utils import format_validation_errors, retry_on_validation_error
from app.utils.llm_factory import get_chat_model
from app.utils.rag_vectorstore import query_all_chunks

logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 6000

_CONTENT_MCQ_PROMPT = """Generate {count} multiple-choice diagnostic question(s) based ONLY on the educational content below.

Topic / section: {topic}

Content:
{content}

Rules:
- Each question must have exactly 4 unique options labeled A, B, C, D.
- One clearly correct answer per question grounded in the content.
- Distractors should reflect common student mistakes.
- Use the latex field when math notation is needed.
- Set conversion_confidence between 0 and 1 for each question.
- Set learning_concept to a short student-friendly skill name (3-8 words) for what the question tests — not the PDF heading or document title.
- Do not invent facts not supported by the content.
{retry_hint}
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


def generate_mcqs_from_content(
    content: str,
    topic: str,
    count: int,
) -> List[MCQQuestion]:
    """Generate validated MCQs from PDF section text using structured LLM output."""
    if not content.strip():
        raise ValueError(f"No content available for topic: {topic}")
    if count < 1:
        return []
    if count > 10:
        count = 10

    llm = get_chat_model(temperature=0.4, max_tokens=4096)
    structured = llm.with_structured_output(MCQBatchResult)
    retry_hint = ""

    def _invoke() -> MCQBatchResult:
        prompt = _CONTENT_MCQ_PROMPT.format(
            count=count,
            topic=topic,
            content=content,
            retry_hint=retry_hint,
        )
        batch: MCQBatchResult = structured.invoke(prompt)
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
