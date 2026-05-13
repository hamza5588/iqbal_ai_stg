"""Misconception vs knowledge gap vs clarification — provider-agnostic."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def classify_understanding_heuristic(question: str) -> Tuple[str, float, Dict[str, Any]]:
    """
    Fast offline classifier when LLM disabled.
    Returns (label, confidence, meta).
    """
    q = (question or "").lower().strip()
    meta: Dict[str, Any] = {"engine": "heuristic"}
    if not q:
        return "clarification", 0.3, meta

    if any(x in q for x in ("why is", "why does", "confused", "wrong", "mistake", "misunderstand")):
        return "misconception", 0.65, meta
    if any(x in q for x in ("what is", "define", "formula", "don't understand", "never learned")):
        return "knowledge_gap", 0.6, meta
    if re.search(r"\b(how do|how does|steps?|prove|show that)\b", q):
        return "clarification", 0.55, meta
    return "clarification", 0.45, meta


def classify_understanding_llm(question: str, api_key: Optional[str]) -> Optional[Tuple[str, float, Dict[str, Any]]]:
    """Optional Groq JSON classification."""
    if not api_key or os.getenv("PHASE3_DISABLE_LLM_CLASSIFY", "").lower() in ("1", "true", "yes"):
        return None
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser
        from langchain_groq import ChatGroq

        prompt = ChatPromptTemplate.from_template(
            """Classify the student's question into exactly one label:
- misconception — shows wrong belief or contradicts facts
- knowledge_gap — missing prerequisite knowledge
- clarification — needs clearer explanation or steps

Respond with JSON only: {{"label":"misconception|knowledge_gap|clarification","confidence":0.0-1.0}}

Question: {q}
"""
        )
        llm = ChatGroq(api_key=api_key, model_name=os.getenv("PHASE3_CLASSIFY_MODEL", "llama-3.1-8b-instant"))
        chain = prompt | llm | StrOutputParser()
        raw = chain.invoke({"q": question[:2000]})
        m = json.loads(raw.strip().replace("```json", "").replace("```", "").strip())
        label = str(m.get("label", "clarification"))
        if label not in ("misconception", "knowledge_gap", "clarification"):
            label = "clarification"
        conf = float(m.get("confidence", 0.7))
        return label, conf, {"engine": "groq", "raw": raw[:500]}
    except Exception as exc:
        logger.warning("LLM classify failed: %s", exc)
        return None


def classify_question(question: str, api_key: Optional[str] = None) -> Tuple[str, float, Dict[str, Any]]:
    llm = classify_understanding_llm(question, api_key)
    if llm:
        return llm
    return classify_understanding_heuristic(question)
