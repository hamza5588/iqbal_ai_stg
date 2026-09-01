"""Generate fresh practice MCQs for weak-topic learning paths (not diagnostic reuse)."""
from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import ValidationError

from app.services.quiz.models import MCQBatchResult, MCQQuestion
from app.services.quiz.retry_utils import format_validation_errors, retry_on_validation_error
from app.utils.llm_factory import get_chat_model

logger = logging.getLogger(__name__)

_REMEDIATION_PROMPT = """Generate {count} NEW practice multiple-choice question(s) for a student who needs help in this topic.

Curriculum topic: {topic_name}
Topic description: {topic_description}
Student recent score on this topic: {score_percent}%
Target difficulty: {difficulty}
Practice focus: {purpose_label}

Rules:
- These are PRACTICE questions — NOT the same style as a broad diagnostic screener.
- Focus on the specific skills and common mistakes for this topic at the given difficulty.
- Do NOT copy or lightly rephrase any question listed under "Already used (avoid repeating)".
- Each question must have exactly 4 unique options (A, B, C, D) and one clearly correct answer.
- Use the latex field when math notation is needed.
- Set conversion_confidence between 0 and 1.
{retry_hint}

Already used (avoid repeating):
{exclude_block}
"""


def generate_remediation_mcqs(
    topic_name: str,
    topic_description: str,
    count: int,
    score_percent: float = 0.0,
    difficulty: str = "medium",
    purpose: str = "practice",
    exclude_question_texts: Optional[List[str]] = None,
) -> List[MCQQuestion]:
    """Generate topic-targeted practice MCQs distinct from diagnostic items."""
    if count < 1:
        return []
    count = min(count, 8)

    exclude = exclude_question_texts or []
    exclude_block = (
        "\n".join(f"- {t[:200]}" for t in exclude[:15])
        if exclude
        else "(none — still create original practice questions)"
    )
    purpose_label = (
        "Quick reassessment after practice"
        if purpose == "reassessment"
        else "Guided practice on weak areas"
    )

    llm = get_chat_model(temperature=0.55, max_tokens=4096)
    structured = llm.with_structured_output(MCQBatchResult)
    retry_hint = ""

    def _invoke() -> MCQBatchResult:
        prompt = _REMEDIATION_PROMPT.format(
            count=count,
            topic_name=topic_name,
            topic_description=topic_description or topic_name,
            score_percent=round(score_percent, 1),
            difficulty=difficulty,
            purpose_label=purpose_label,
            exclude_block=exclude_block,
            retry_hint=retry_hint,
        )
        batch: MCQBatchResult = structured.invoke(prompt)
        if not batch.questions:
            raise ValidationError.from_exception_data(
                "MCQBatchResult",
                [{"type": "value_error", "loc": ("questions",), "msg": "No questions generated", "input": []}],
            )
        return batch

    def _on_retry(exc: ValidationError, attempt: int) -> None:
        nonlocal retry_hint
        retry_hint = f"\nPrevious attempt {attempt} failed: {format_validation_errors(exc)}."

    batch = retry_on_validation_error(_invoke, max_retries=2, on_retry=_on_retry)
    return batch.questions[:count]
