"""Topic clustering from prep-book OCR text (LLM optional + heuristic fallback)."""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STOP = {
    "that",
    "this",
    "with",
    "from",
    "have",
    "been",
    "were",
    "they",
    "will",
    "would",
    "there",
    "their",
    "which",
    "about",
    "these",
    "those",
    "other",
    "into",
    "than",
    "then",
    "some",
    "such",
    "what",
    "when",
    "your",
    "also",
    "each",
    "more",
    "very",
}


def _topics_heuristic(text: str, *, max_topics: int = 15) -> List[Dict[str, Any]]:
    cleaned = re.sub(r"[^\w\s\-]", " ", text.lower())
    words = [w for w in cleaned.split() if len(w) >= 4 and w not in _STOP]
    counts = Counter(words)
    out: List[Dict[str, Any]] = []
    for w, n in counts.most_common(max_topics):
        out.append({"topic": w.title(), "frequency": int(n), "importance": round(min(1.0, n / max(20, len(words) / 10)), 3)})
    return out


def _topics_llm(text: str, *, groq_api_key: str, model_name: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    sample = text[:12_000]
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser
        from langchain_groq import ChatGroq

        prompt = ChatPromptTemplate.from_template(
            "From this prep-book excerpt, list the main study topics (8–15). "
            "Return JSON only: one object with key \"topics\" (array of objects with "
            'keys topic (string), frequency (integer), importance (number 0–1)).\n\n---\n{t}'
        )
        llm = ChatGroq(
            api_key=groq_api_key,
            model_name=model_name or os.getenv("PHASE3_TOPIC_MODEL", "llama-3.1-8b-instant"),
        )
        chain = prompt | llm | StrOutputParser()
        raw = chain.invoke({"t": sample})
        cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned)
        topics = data.get("topics") or []
        if isinstance(topics, list) and topics:
            normalized = []
            for item in topics[:20]:
                if isinstance(item, dict) and item.get("topic"):
                    normalized.append(
                        {
                            "topic": str(item["topic"])[:300],
                            "frequency": int(item.get("frequency") or 1),
                            "importance": float(item.get("importance") or 0.5),
                        }
                    )
            return normalized if normalized else None
    except Exception as exc:
        logger.warning("prep-book topic LLM failed: %s", exc)
    return None


def extract_topics_from_prep_text(
    text: str,
    *,
    groq_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Returns payload dict with topics list and method used."""
    if not (text or "").strip():
        return {"topics": [], "method": "empty", "note": "No OCR text available"}

    if groq_api_key and os.getenv("PHASE3_SKIP_TOPIC_LLM", "").lower() not in ("1", "true", "yes"):
        llm_topics = _topics_llm(text, groq_api_key=groq_api_key)
        if llm_topics:
            return {"topics": llm_topics, "method": "llm"}

    heur = _topics_heuristic(text)
    return {
        "topics": heur,
        "method": "heuristic",
        "note": "LLM disabled or unavailable — keyword clusters from OCR text",
    }
