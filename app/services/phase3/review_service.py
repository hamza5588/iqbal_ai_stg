"""Next-day review aggregates from Phase 3 captured questions + legacy FAQs."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.database_models import LessonFAQ as DBLessonFAQ, Lesson as DBLesson
from app.models.phase3_models import LectureTeacherReview, StudentLearningQuestion


def next_day_review_payload(db: Session, *, lesson_id: int) -> Dict[str, Any]:
    lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    summary = None
    rev = (
        db.query(LectureTeacherReview)
        .filter(LectureTeacherReview.lesson_id == lesson_id)
        .order_by(desc(LectureTeacherReview.updated_at))
        .first()
    )
    if rev and rev.ai_summary:
        summary = rev.ai_summary
    elif lesson:
        summary = (
            f"Review snapshot for **{lesson.title}**. "
            "Students asked practice questions below — use mini-lectures to remediate clusters."
        )

    canonical_expr = func.coalesce(DBLessonFAQ.canonical_question, DBLessonFAQ.question)
    faq_rows = (
        db.query(canonical_expr.label("question"), func.sum(DBLessonFAQ.count).label("count"))
        .filter(DBLessonFAQ.lesson_id == lesson_id)
        .group_by(canonical_expr)
        .order_by(desc("count"))
        .limit(15)
        .all()
    )
    most_asked = [{"question": r[0], "count": int(r[1] or 0)} for r in faq_rows]

    since = datetime.utcnow() - timedelta(days=14)
    slq = (
        db.query(StudentLearningQuestion)
        .filter(
            StudentLearningQuestion.lesson_id == lesson_id,
            StudentLearningQuestion.created_at >= since,
        )
        .order_by(desc(StudentLearningQuestion.created_at))
        .limit(200)
        .all()
    )
    from collections import Counter

    fp_counter = Counter()
    fp_example: Dict[str, str] = {}
    critical_list: List[Dict[str, Any]] = []
    for row in slq:
        fp = row.canonical_fingerprint or str(row.id)
        fp_counter[fp] += 1
        if fp not in fp_example:
            fp_example[fp] = row.question_text[:500]
        if row.is_critical:
            critical_list.append(
                {
                    "question": row.question_text[:500],
                    "label": row.understanding_label,
                    "mode": row.mode,
                }
            )

    for fp, count in fp_counter.most_common(10):
        if count >= 3:
            critical_list.append(
                {
                    "question": fp_example.get(fp, ""),
                    "label": "high_frequency",
                    "count": count,
                }
            )

    # Dedupe critical by question text
    seen = set()
    deduped = []
    for c in critical_list:
        key = (c.get("question") or "")[:120]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    return {
        "summary": summary,
        "critical_questions": deduped[:20],
        "most_asked_faq": most_asked,
        "phase3_question_count": len(slq),
    }
