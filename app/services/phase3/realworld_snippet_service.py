"""Generate or load syllabus real-world / careers snippets for a topic."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.phase1_models import SyllabusTopic
from app.models.phase3_models import SyllabusRealWorldSnippet

logger = logging.getLogger(__name__)


def _placeholder(topic_title: str) -> Dict[str, Any]:
    return {
        "applications": [
            f'Apply ideas from "{topic_title}" to concrete examples you see in labs or daily life.',
            "Connect definitions to one worked example, then teach it aloud to someone else.",
        ],
        "careers": ["Science & engineering pathways", "Teaching & tutoring", "Data & analytics foundations"],
        "mini_challenges": [
            f'In 3 bullets, summarize why "{topic_title}" matters for your exam.',
            "Sketch a diagram or equation-free explanation for a younger student.",
        ],
        "cached": False,
        "generated": "placeholder",
    }


def _generate_llm(topic_title: str, *, groq_api_key: str) -> Optional[Dict[str, Any]]:
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser
        from langchain_groq import ChatGroq

        prompt = ChatPromptTemplate.from_template(
            'For the syllabus topic "{title}", produce motivating real-world context for students. '
            'Respond as JSON only with keys applications (array of 3 short strings), '
            'careers (array of 3 strings), mini_challenges (array of 3 short exercises).'
        )
        llm = ChatGroq(api_key=groq_api_key, model_name=os.getenv("PHASE3_REALWORLD_MODEL", "llama-3.1-8b-instant"))
        chain = prompt | llm | StrOutputParser()
        raw = chain.invoke({"title": topic_title[:500]})
        cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned)
        data["cached"] = False
        data["generated"] = "llm"
        return data
    except Exception as exc:
        logger.warning("realworld LLM generation failed: %s", exc)
        return None


def get_or_create_snippet_payload(
    db: Session,
    *,
    syllabus_topic_id: int,
    groq_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    row = (
        db.query(SyllabusRealWorldSnippet)
        .filter(SyllabusRealWorldSnippet.syllabus_topic_id == syllabus_topic_id)
        .first()
    )
    if row:
        try:
            payload = json.loads(row.payload_json)
            payload["cached"] = True
            return payload
        except Exception:
            pass

    topic = db.query(SyllabusTopic).filter(SyllabusTopic.id == syllabus_topic_id).first()
    title = topic.title if topic else f"Topic {syllabus_topic_id}"

    payload: Dict[str, Any]
    if groq_api_key and os.getenv("PHASE3_SKIP_REALWORLD_LLM", "").lower() not in ("1", "true", "yes"):
        payload = _generate_llm(title, groq_api_key=groq_api_key) or _placeholder(title)
    else:
        payload = _placeholder(title)

    pj = json.dumps(payload, default=str)
    if row:
        row.payload_json = pj
    else:
        db.add(SyllabusRealWorldSnippet(syllabus_topic_id=syllabus_topic_id, payload_json=pj))
    db.commit()
    return payload
