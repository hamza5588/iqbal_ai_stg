"""Compute anonymised positive cohort metrics from learning events when no benchmark row exists."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.models.phase3_models import LearningEvent
from app.models.school_org_models import ClassEnrollment

logger = logging.getLogger(__name__)


def compute_section_activity_metrics(
    db: Session,
    *,
    class_section_id: int,
    window_days: int = 14,
) -> Dict[str, Any]:
    """Aggregate recent LearningEvent rows for students enrolled in the section."""
    since_dt = datetime.combine(date.today() - timedelta(days=window_days), datetime.min.time())
    student_rows = (
        db.query(ClassEnrollment.student_user_id)
        .filter(ClassEnrollment.class_section_id == class_section_id, ClassEnrollment.status == "active")
        .all()
    )
    student_ids: List[int] = [int(r[0]) for r in student_rows]
    if not student_ids:
        return {
            "message": "Your cohort is growing — activity metrics appear once classmates start studying.",
            "peer_engagement_trend": "steady",
            "cohort_size": 0,
            "computed_from_events": True,
        }

    total_events = (
        db.query(func.count(LearningEvent.id))
        .filter(LearningEvent.student_user_id.in_(student_ids))
        .filter(LearningEvent.created_at >= since_dt)
        .scalar()
        or 0
    )
    active_learners = (
        db.query(func.count(distinct(LearningEvent.student_user_id)))
        .filter(LearningEvent.student_user_id.in_(student_ids))
        .filter(LearningEvent.created_at >= since_dt)
        .scalar()
        or 0
    )

    intensity = total_events / max(1, len(student_ids) * 3)
    trend = "up" if total_events > len(student_ids) * 2 else "steady"

    return {
        "message": "Your cohort is building momentum — keep your daily streak.",
        "peer_engagement_trend": trend,
        "cohort_activity_index": round(min(1.0, intensity / 10.0), 2),
        "active_learners_window": int(active_learners),
        "cohort_size": len(student_ids),
        "events_in_window": int(total_events),
        "window_days": window_days,
        "computed_from_events": True,
    }


def merge_benchmark_or_compute(
    db: Session,
    *,
    class_section_id: int,
    stored_metrics_json: str | None,
) -> Dict[str, Any]:
    if stored_metrics_json:
        try:
            return json.loads(stored_metrics_json)
        except Exception:
            logger.debug("bad benchmark json, recomputing")
    return compute_section_activity_metrics(db, class_section_id=class_section_id)
