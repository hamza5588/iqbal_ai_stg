"""
Explicit teacher-triggered delivery guidance (never invoked from lesson generation).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

from langchain_core.messages import HumanMessage
from sqlalchemy.orm import Session

from app.models.database_models import Lesson as DBLesson
from app.models.school_learning_models import LessonDeliveryGuidance
from app.services.school.errors import SchoolServiceError
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


def request_delivery_guidance(db: Session, *, teacher_user_id: int, lesson_id: int) -> Dict[str, Any]:
    """
    Generate and persist delivery guidance for one lesson.

    Returns structured fields:
      - delivery_tips: list[str] (3 items)
      - technique_demonstration: str
      - real_world_examples: list[str] (2 items, not from textbook)
    """
    lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    if not lesson:
        raise SchoolServiceError("Lesson not found", "not_found", 404)
    if lesson.teacher_id != teacher_user_id:
        raise SchoolServiceError("You can only request guidance for your own lessons", "forbidden", 403)

    excerpt = (lesson.content or "")[:16000]
    prompt = (
        "You are an expert pedagogy coach. Return ONLY JSON with keys: "
        '"delivery_tips" (array of exactly 3 short strings), '
        '"technique_demonstration" (one string describing one concrete classroom technique), '
        '"real_world_examples" (array of exactly 2 short strings illustrating applications '
        "outside the textbook, not quoting the text). Base suggestions on this lesson.\n\n"
        f"LESSON:\n{excerpt}"
    )

    try:
        model = get_chat_model(None, temperature=0.2)
        raw = model.invoke([HumanMessage(content=prompt)])
        content = raw.content if hasattr(raw, "content") else str(raw)
        data = _extract_json_obj(content)
    except Exception as e:
        logger.warning("delivery_guidance LLM failed: %s", e)
        data = {
            "delivery_tips": [
                "Open with one question that previews the objective.",
                "Chunk explanations into 5–7 minute segments with quick checks.",
                "Close by tying student work back to the success criteria.",
            ],
            "technique_demonstration": "Use a think-pair-share after the first worked example.",
            "real_world_examples": [
                "Relate the concept to household budgeting or scheduling decisions.",
                "Connect to a local news item that illustrates cause and effect.",
            ],
        }

    tips = data.get("delivery_tips") or []
    tech = str(data.get("technique_demonstration") or "")
    exs = data.get("real_world_examples") or []
    if not isinstance(tips, list):
        tips = []
    if not isinstance(exs, list):
        exs = []
    tips = [str(x) for x in tips[:3]]
    while len(tips) < 3:
        tips.append("Invite brief student reflection before moving on.")
    exs = [str(x) for x in exs[:2]]
    while len(exs) < 2:
        exs.append("Offer a short analogy from everyday life.")

    payload = {
        "delivery_tips": tips[:3],
        "technique_demonstration": tech or "Model one example aloud, then co-construct a second.",
        "real_world_examples": exs[:2],
    }
    blob = json.dumps(payload)

    row = db.query(LessonDeliveryGuidance).filter(LessonDeliveryGuidance.lesson_id == lesson_id).first()
    if row:
        row.teacher_user_id = teacher_user_id
        row.payload_json = blob
    else:
        row = LessonDeliveryGuidance(
            lesson_id=lesson_id,
            teacher_user_id=teacher_user_id,
            payload_json=blob,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    out = dict(payload)
    out["lesson_id"] = lesson_id
    out["updated_at"] = row.updated_at.isoformat() if row.updated_at else None
    return out
