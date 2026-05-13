"""Optional LLM narrative on top of syllabus-driven plan skeleton."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.services.phase3.study_plan_service import build_plan_skeleton, plan_to_json


def build_conversational_plan(
    db: Session,
    *,
    student_transcript: str,
    exam_type_id: int,
    grade: str,
    platform_subject_id: int,
    horizon_days: int,
    hours_per_day: float,
    weak_topic_ids: Optional[list] = None,
    groq_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    base = build_plan_skeleton(
        db,
        exam_type_id=exam_type_id,
        grade=grade,
        platform_subject_id=platform_subject_id,
        horizon_days=horizon_days,
        hours_per_day=hours_per_day,
        weak_topic_ids=weak_topic_ids,
    )
    base["conversation_transcript"] = (student_transcript or "")[:8000]
    if not groq_api_key:
        groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key or os.getenv("PHASE3_SKIP_CONVERSATIONAL_LLM", "").lower() in ("1", "true", "yes"):
        base["coach_note"] = "LLM coach disabled — edit plan JSON in UI."
        return base
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser
        from langchain_groq import ChatGroq

        prompt = ChatPromptTemplate.from_template(
            "You are a study coach. Given the student's goals transcript and a machine skeleton plan, "
            "write a short actionable coach_note (max 120 words) and 3 bullet priorities.\n"
            "Transcript:\n{t}\n\nSkeleton JSON summary:\n{s}\n"
            'Respond as JSON only: {{"coach_note":"...","priorities":["","",""]}}'
        )
        llm = ChatGroq(api_key=groq_api_key, model_name=os.getenv("PHASE3_PLAN_MODEL", "llama-3.1-8b-instant"))
        chain = prompt | llm | StrOutputParser()
        raw = chain.invoke(
            {
                "t": student_transcript[:4000],
                "s": plan_to_json(base)[:6000],
            }
        )
        import json

        extra = json.loads(raw.strip().replace("```json", "").replace("```", "").strip())
        base["coach_note"] = extra.get("coach_note", "")
        base["priorities"] = extra.get("priorities", [])
    except Exception:
        base["coach_note"] = "Coach narrative unavailable — using syllabus skeleton only."
    return base
