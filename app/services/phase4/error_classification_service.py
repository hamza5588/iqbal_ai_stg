"""Wrong-answer error type: careless | conceptual | misunderstanding (provider-agnostic)."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

LABELS = ("careless", "conceptual", "misunderstanding")


def classify_wrong_answer_heuristic(
    *,
    duration_ms: Optional[int],
    confidence_1_5: Optional[int],
    stem_snippet: str = "",
) -> Tuple[str, str, Dict[str, Any]]:
    meta: Dict[str, Any] = {"engine": "heuristic"}
    c = confidence_1_5 or 3
    d = duration_ms
    if d is not None and d < 3000 and c >= 4:
        return "careless", "Quick wrong answer with high confidence often indicates a slip.", meta
    if c <= 2:
        return "misunderstanding", "Low confidence suggests the question may not have been understood.", meta
    s = (stem_snippet or "").lower()
    if any(x in s for x in ("prove", "derive", "explain why", "concept")):
        return "conceptual", "The item looks concept-heavy; gaps may be in underlying ideas.", meta
    return "conceptual", "Wrong answer with moderate confidence — review the core concept.", meta


def classify_wrong_answer_llm(
    *,
    stem: str,
    chosen_answer: str,
    correct_answer: str,
) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    if os.getenv("PHASE4_DISABLE_LLM_ERROR_CLASSIFY", "").lower() in ("1", "true", "yes"):
        return None
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        from app.utils.llm_factory import get_chat_model

        llm = get_chat_model()
        prompt = ChatPromptTemplate.from_template(
            """You classify a student's wrong answer on a multiple-choice style question.
Pick exactly one: careless | conceptual | misunderstanding

careless — slip, misread, arithmetic slip
conceptual — missing or wrong underlying principle
misunderstanding — misread the question or confused similar ideas

Return JSON only: {{"label":"...","explanation":"one short sentence"}}

Stem: {stem}
Student answer: {chosen}
Correct: {correct}
"""
        )
        chain = prompt | llm | StrOutputParser()
        raw = chain.invoke(
            {"stem": stem[:1500], "chosen": chosen_answer[:500], "correct": correct_answer[:500]}
        )
        m = json.loads(raw.strip().replace("```json", "").replace("```", "").strip())
        label = str(m.get("label", "conceptual"))
        if label not in LABELS:
            label = "conceptual"
        expl = str(m.get("explanation", "")).strip() or "Review the explanation and try a similar question."
        return label, expl, {"engine": "llm", "raw": raw[:800]}
    except Exception as exc:
        logger.debug("LLM error classify skipped: %s", exc)
        return None


def classify_wrong_answer(
    *,
    stem: str = "",
    chosen_answer: str = "",
    correct_answer: str = "",
    duration_ms: Optional[int] = None,
    confidence_1_5: Optional[int] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    llm = classify_wrong_answer_llm(stem=stem, chosen_answer=chosen_answer, correct_answer=correct_answer)
    if llm:
        return llm
    return classify_wrong_answer_heuristic(
        duration_ms=duration_ms, confidence_1_5=confidence_1_5, stem_snippet=stem
    )
