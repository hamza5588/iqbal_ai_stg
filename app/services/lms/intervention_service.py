"""Rule-based intervention recommendations (Phase 8)."""
from __future__ import annotations

from typing import List, Optional

from app.services.lms.analytics_service import aggregate_class_topics, get_struggling_students
from app.services.lms.assessment_service import list_assessments_by_teacher
from app.services.lms.class_service import teacher_owns_class
from app.services.lms.exceptions import LMSValidationError
from app.services.lms.performance_service import get_student_mastery, WEAK_THRESHOLD


def recommend_for_student(student_id: int, topic_id: Optional[int] = None) -> List[dict]:
    mastery = get_student_mastery(student_id)
    weak = [m for m in mastery if m.get("mastery_status") == "weak" or m.get("score_percent", 100) < WEAK_THRESHOLD]
    if topic_id:
        weak = [m for m in weak if m["topic_id"] == topic_id]
    recs = []
    for w in weak[:5]:
        recs.append(
            {
                "type": "review_topic",
                "topic_id": w["topic_id"],
                "priority": "high" if w.get("score_percent", 0) < 40 else "medium",
                "message": f"Review topic #{w['topic_id']} — score {w.get('score_percent', 0):.0f}%",
                "actions": ["assign_practice_quiz", "schedule_reassessment"],
            }
        )
    if not recs:
        recs.append({"type": "on_track", "message": "No interventions needed.", "priority": "low"})
    return recs


def recommend_for_class(class_id: int, teacher_id: int) -> dict:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    topics = aggregate_class_topics(class_id, teacher_id)
    struggling = get_struggling_students(class_id, teacher_id)
    class_recs = []
    for t in topics:
        if t.get("weak_student_count", 0) >= 2:
            class_recs.append(
                {
                    "type": "class_review",
                    "topic_id": t["topic_id"],
                    "topic_name": t["topic_name"],
                    "weak_student_count": t["weak_student_count"],
                    "message": f"{t['weak_student_count']} students struggle with {t['topic_name']}",
                    "actions": ["assign_class_quiz", "review_lesson"],
                }
            )
    student_recs = [
        {
            "student_id": s["student_id"],
            "username": s.get("username"),
            "recommendations": recommend_for_student(s["student_id"]),
        }
        for s in struggling[:10]
    ]
    return {"class_recommendations": class_recs, "student_recommendations": student_recs}


def auto_assign_intervention(
    teacher_id: int,
    class_id: int,
    quiz_id: int,
    title: str,
) -> dict:
    from app.services.lms import assignment_service

    a = assignment_service.create_assignment(
        teacher_id=teacher_id,
        class_id=class_id,
        quiz_id=quiz_id,
        title=title,
    )
    published = assignment_service.publish_assignment(a.id, teacher_id)
    return {"assignment_id": published.id, "status": published.status}
