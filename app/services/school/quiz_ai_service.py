"""AI-generated MCQs from lecture content (teacher-triggered)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage

from app.utils.llm_factory import get_chat_model

logger = logging.getLogger(__name__)


def _extract_json_obj(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("no_json")
    return json.loads(m.group(0))


def generate_mcq_payload(lesson_markdown: str, num_questions: int = 5) -> List[Dict[str, Any]]:
    """
    Produce validated MCQ list. On LLM/parse failure returns a small safe fallback
    so classroom flow is never hard-blocked.
    """
    n = max(1, min(int(num_questions), 20))
    excerpt = (lesson_markdown or "")[:12000]
    if not excerpt.strip():
        return _fallback_questions(n)

    prompt = (
        f"You are an assessment author for a classroom MCQ check (~40 min into a lesson). "
        f"Return ONLY valid JSON with a single key \"questions\" whose value is an array of "
        f"exactly {n} objects. Each object must have: "
        '"id" (short string), "prompt" (string), "choices" (array of exactly 4 distinct strings), '
        '"correct_index" (integer 0-3). '
        "Base every question strictly on the lecture excerpt; do not invent facts not supported "
        "by the text.\n\nLECTURE EXCERPT:\n"
        f"{excerpt}"
    )

    try:
        model = get_chat_model(None, temperature=0.15)
        raw = model.invoke([HumanMessage(content=prompt)])
        content = raw.content if hasattr(raw, "content") else str(raw)
        data = _extract_json_obj(content)
        questions = data.get("questions")
        if not isinstance(questions, list):
            raise ValueError("missing_questions_array")
        cleaned: List[Dict[str, Any]] = []
        for i, q in enumerate(questions[:n]):
            if not isinstance(q, dict):
                continue
            choices = q.get("choices")
            if not isinstance(choices, list) or len(choices) != 4:
                continue
            ci = q.get("correct_index")
            if not isinstance(ci, int) or ci < 0 or ci > 3:
                continue
            cleaned.append(
                {
                    "id": str(q.get("id") or f"q{i+1}"),
                    "prompt": str(q.get("prompt") or "")[:2000],
                    "choices": [str(c)[:500] for c in choices],
                    "correct_index": ci,
                }
            )
        if len(cleaned) < n:
            cleaned.extend(_fallback_questions(n - len(cleaned)))
        return cleaned[:n]
    except Exception as e:
        logger.warning("quiz_ai_service.generate_mcq_payload: falling back (%s)", e)
        return _fallback_questions(n)


def _fallback_questions(n: int) -> List[Dict[str, Any]]:
    out = []
    for i in range(n):
        out.append(
            {
                "id": f"fb{i+1}",
                "prompt": "According to the lecture, which statement best reflects the main idea reviewed in class?",
                "choices": [
                    "The teacher will clarify this after review.",
                    "The material was not available for auto-quiz.",
                    "Focus on definitions your teacher highlighted.",
                    "Re-read the section your teacher indicated.",
                ],
                "correct_index": 0,
            }
        )
    return out
