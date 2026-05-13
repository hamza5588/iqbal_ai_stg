"""Study plan scaffolding using Phase 1 syllabus topics."""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.phase1_models import ExamType, SyllabusChapter, SyllabusTopic


def build_plan_skeleton(
    db: Session,
    *,
    exam_type_id: int,
    grade: str,
    platform_subject_id: int,
    horizon_days: int,
    hours_per_day: float,
    weak_topic_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Weekly/daily structure without LLM; conversational AI can replace plan_json later."""
    chapters = (
        db.query(SyllabusChapter)
        .filter(
            SyllabusChapter.exam_type_id == exam_type_id,
            SyllabusChapter.platform_subject_id == platform_subject_id,
            SyllabusChapter.grade == grade,
            SyllabusChapter.is_active.is_(True),
        )
        .all()
    )
    topic_rows: List[SyllabusTopic] = []
    for ch in chapters:
        for t in ch.topics:
            if t.is_active:
                topic_rows.append(t)
    # Fallback if relationship not loaded
    if not topic_rows:
        topic_rows = (
            db.query(SyllabusTopic)
            .join(SyllabusChapter, SyllabusTopic.chapter_id == SyllabusChapter.id)
            .filter(
                SyllabusChapter.exam_type_id == exam_type_id,
                SyllabusChapter.platform_subject_id == platform_subject_id,
                SyllabusChapter.grade == grade,
            )
            .order_by(SyllabusTopic.order_index)
            .all()
        )

    weak_set = set(weak_topic_ids or [])
    today = date.today()
    weeks: List[Dict[str, Any]] = []
    if horizon_days <= 14:
        day_blocks = []
        for i in range(min(horizon_days, 14)):
            d = today + timedelta(days=i)
            idx = i % max(len(topic_rows), 1)
            tid = topic_rows[idx].id if topic_rows else None
            title = topic_rows[idx].title if topic_rows else "Review"
            priority = "high" if tid in weak_set else "normal"
            day_blocks.append(
                {
                    "date": d.isoformat(),
                    "focus_topic_id": tid,
                    "focus_title": title,
                    "planned_minutes": int(hours_per_day * 60),
                    "priority": priority,
                }
            )
        weeks.append({"label": "Daily detail", "days": day_blocks})
    else:
        # Long horizon: weekly overview + detail for current week only
        detail_days = []
        for i in range(7):
            d = today + timedelta(days=i)
            idx = i % max(len(topic_rows), 1)
            tid = topic_rows[idx].id if topic_rows else None
            title = topic_rows[idx].title if topic_rows else "Review"
            detail_days.append(
                {
                    "date": d.isoformat(),
                    "focus_topic_id": tid,
                    "focus_title": title,
                    "planned_minutes": int(hours_per_day * 60),
                }
            )
        overview_weeks = []
        num_weeks = max(1, horizon_days // 7)
        for w in range(min(num_weeks, 12)):
            overview_weeks.append(
                {
                    "week_index": w + 1,
                    "themes": [t.title for t in topic_rows[:5]],
                    "note": "Overview — adjust in editor",
                }
            )
        weeks.append({"label": "Weekly overview", "weeks": overview_weeks})
        weeks.append({"label": "Current week detail", "days": detail_days})

    exam_label = db.query(ExamType).filter(ExamType.id == exam_type_id).first()
    return {
        "version": 1,
        "exam_type_id": exam_type_id,
        "exam_type_name": exam_label.name if exam_label else None,
        "grade": grade,
        "platform_subject_id": platform_subject_id,
        "horizon_days": horizon_days,
        "hours_per_day": hours_per_day,
        "sections": weeks,
        "generated_from": "syllabus_engine",
        "raw_topics_count": len(topic_rows),
    }


def plan_to_json(plan: Dict[str, Any]) -> str:
    return json.dumps(plan, default=str)
