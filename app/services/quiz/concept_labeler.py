"""Assign real learning-concept labels to diagnostic MCQs (never 'General')."""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from app.services.quiz.models import MCQQuestion
from app.services.quiz.retry_utils import invoke_structured

logger = logging.getLogger(__name__)

_GENERIC = {
    "general",
    "general practice",
    "practice",
    "practice area",
    "misc",
    "miscellaneous",
    "other",
    "unknown",
    "n/a",
    "na",
}


class ConceptLabel(BaseModel):
    index: int = Field(..., description="0-based index matching the input batch")
    learning_concept: str = Field(
        ...,
        description="Short subject topic name, 2-5 words (e.g. Fractions, Linear Equations)",
    )


class ConceptLabelBatch(BaseModel):
    labels: List[ConceptLabel] = Field(default_factory=list)


_CONCEPT_PROMPT = """You label maths diagnostic MCQs with the SUBJECT TOPIC each question tests.

Rules:
- learning_concept = a short curriculum topic (2-5 words), e.g. "Fractions", "Percentages",
  "Linear Equations", "Exponents", "Rational Numbers", "Algebraic Expressions",
  "Arithmetic Sequences", "Profit and Loss".
- Group similar questions under the SAME topic name when they test the same skill.
- Use 3-8 distinct topics across the whole set when the paper covers multiple skills.
- NEVER use "General", "Practice", "Math", "Miscellaneous", or document/PDF titles.
- NEVER copy the full question text as the topic name.

Questions JSON (array of {{index, question_text, options}}):
{payload}
"""


def is_generic_concept(label: Optional[str]) -> bool:
    return (label or "").strip().lower() in _GENERIC


def assign_learning_concepts(mcqs: List[MCQQuestion]) -> List[MCQQuestion]:
    """Fill missing/generic learning_concept via LLM. No-op on failure."""
    if not mcqs:
        return mcqs

    need_idx = [
        i
        for i, m in enumerate(mcqs)
        if is_generic_concept(getattr(m, "learning_concept", None))
        or not (getattr(m, "learning_concept", None) or "").strip()
    ]
    if not need_idx:
        return mcqs

    from app.utils.llm_factory import get_chat_model

    payload = []
    for i in need_idx:
        m = mcqs[i]
        opts = [getattr(o, "text", "") or "" for o in (m.options or [])][:4]
        payload.append(
            {
                "index": i,
                "question_text": (m.question_text or "")[:300],
                "options": opts,
            }
        )

    llm = get_chat_model(temperature=0.1, max_tokens=2048)
    by_index: dict[int, str] = {}
    batch_size = 10
    for start in range(0, len(payload), batch_size):
        chunk = payload[start : start + batch_size]
        try:
            result: ConceptLabelBatch = invoke_structured(
                llm,
                ConceptLabelBatch,
                _CONCEPT_PROMPT.format(payload=json.dumps(chunk, ensure_ascii=False)),
            )
        except Exception as exc:
            logger.warning("Concept labeling batch failed: %s", exc)
            continue
        for item in result.labels or []:
            name = (item.learning_concept or "").strip()
            if name and not is_generic_concept(name):
                by_index[int(item.index)] = name[:80]

    if not by_index:
        return mcqs

    out: List[MCQQuestion] = []
    for i, m in enumerate(mcqs):
        label = by_index.get(i)
        if label and hasattr(m, "model_copy"):
            out.append(m.model_copy(update={"learning_concept": label}))
        else:
            out.append(m)
    return out
