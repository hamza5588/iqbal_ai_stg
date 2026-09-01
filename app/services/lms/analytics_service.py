"""Class and student analytics (Phase 7)."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from app.models.database_models import User as DBUser
from app.models.lms_models import (
    Assessment,
    AssessmentAttempt,
    Assignment,
    AssignmentSubmission,
    MasterySnapshot,
    Question,
    StudentTopicScore,
    Topic,
)
from app.services.lms.class_service import list_class_students, teacher_owns_class
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.performance_service import WEAK_THRESHOLD, get_overall_progress, get_student_mastery
from app.utils.db import get_db


def aggregate_class_topics(class_id: int, teacher_id: int) -> List[dict]:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    enrollments = list_class_students(class_id)
    student_ids = [e.student_id for e in enrollments]
    if not student_ids:
        return []

    rows = (
        db.query(StudentTopicScore)
        .filter(StudentTopicScore.student_id.in_(student_ids))
        .all()
    )
    by_topic: dict[int, dict] = {}
    for r in rows:
        bucket = by_topic.setdefault(
            r.topic_id,
            {"topic_id": r.topic_id, "scores": [], "weak_students": []},
        )
        bucket["scores"].append(r.score_percent)
        if r.score_percent < WEAK_THRESHOLD:
            bucket["weak_students"].append(
                {"student_id": r.student_id, "score_percent": r.score_percent}
            )

    weak_student_ids = {
        s["student_id"] for stats in by_topic.values() for s in stats["weak_students"]
    }
    users = (
        {
            u.id: u
            for u in db.query(DBUser).filter(DBUser.id.in_(weak_student_ids)).all()
        }
        if weak_student_ids
        else {}
    )

    topics = {t.id: t for t in db.query(Topic).filter(Topic.id.in_(by_topic.keys())).all()}
    result = []
    for tid, stats in by_topic.items():
        topic = topics.get(tid)
        avg = sum(stats["scores"]) / len(stats["scores"]) if stats["scores"] else 0
        weak_students = []
        for s in stats["weak_students"]:
            user = users.get(s["student_id"])
            weak_students.append(
                {
                    "student_id": s["student_id"],
                    "username": user.username if user else f"Student #{s['student_id']}",
                    "score_percent": round(s["score_percent"], 1),
                }
            )
        weak_students.sort(key=lambda x: (x["username"] or "").lower())
        result.append(
            {
                "topic_id": tid,
                "topic_name": topic.name if topic else f"Topic #{tid}",
                "avg_score": round(avg, 2),
                "weak_student_count": len(weak_students),
                "weak_students": weak_students,
                "students_assessed": len(set(student_ids)),
            }
        )
    result.sort(key=lambda x: x["avg_score"])
    return result


def get_class_quiz_results(class_id: int, teacher_id: int) -> List[dict]:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    assignments = (
        db.query(Assignment)
        .filter(Assignment.class_id == class_id, Assignment.status == "published")
        .all()
    )
    enrollments = list_class_students(class_id)
    total_students = len(enrollments)
    student_ids = [e.student_id for e in enrollments]
    users = (
        {
            u.id: u
            for u in db.query(DBUser).filter(DBUser.id.in_(student_ids)).all()
        }
        if student_ids
        else {}
    )
    results = []
    for a in assignments:
        subs = (
            db.query(AssignmentSubmission)
            .filter(AssignmentSubmission.assignment_id == a.id)
            .all()
        )
        subs_by_student = {s.student_id: s for s in subs}
        attempt_ids = [s.attempt_id for s in subs if s.attempt_id]
        attempts = (
            {
                att.id: att
                for att in db.query(AssessmentAttempt)
                .filter(AssessmentAttempt.id.in_(attempt_ids))
                .all()
            }
            if attempt_ids
            else {}
        )
        submitted = [s for s in subs if s.status == "submitted"]
        scores = []
        student_results = []
        for student_id in student_ids:
            user = users.get(student_id)
            sub = subs_by_student.get(student_id)
            status = sub.status if sub else "not_started"
            score = None
            max_score = None
            score_percent = None
            if sub and sub.attempt_id:
                att = attempts.get(sub.attempt_id)
                if att:
                    score = att.score
                    max_score = att.max_score
                    if att.max_score:
                        score_percent = round(100.0 * (att.score or 0) / att.max_score, 1)
                        if sub.status == "submitted":
                            scores.append(score_percent)
            student_results.append(
                {
                    "student_id": student_id,
                    "username": user.username if user else f"Student #{student_id}",
                    "status": status,
                    "score": score,
                    "max_score": max_score,
                    "score_percent": score_percent,
                }
            )
        student_results.sort(key=lambda x: (x["username"] or "").lower())
        results.append(
            {
                "assignment_id": a.id,
                "title": a.title,
                "quiz_id": a.quiz_id,
                "due_date": a.due_date.isoformat() if a.due_date else None,
                "total_students": total_students,
                "submitted_count": len(submitted),
                "completion_percent": round(100.0 * len(submitted) / total_students, 1) if total_students else 0,
                "avg_score_percent": round(sum(scores) / len(scores), 1) if scores else None,
                "overdue_count": max(0, total_students - len(submitted)),
                "student_results": student_results,
            }
        )
    return results


def get_class_roster_summary(class_id: int, teacher_id: int) -> List[dict]:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    enrollments = list_class_students(class_id)
    roster = []
    for enr in enrollments:
        user = db.query(DBUser).filter(DBUser.id == enr.student_id).first()
        mastery = get_student_mastery(enr.student_id)
        weak = [m for m in mastery if m.get("mastery_status") == "weak"]
        weak_topics = [
            {
                "topic_id": m["topic_id"],
                "topic_name": m["topic_name"],
                "score_percent": m.get("score_percent"),
            }
            for m in weak
        ]
        weak_topics.sort(key=lambda x: (x["topic_name"] or "").lower())
        roster.append(
            {
                "student_id": enr.student_id,
                "username": user.username if user else None,
                "email": user.useremail if user else None,
                "overall_progress": get_overall_progress(enr.student_id),
                "weak_topic_count": len(weak_topics),
                "weak_topics": weak_topics,
                "is_struggling": len(weak_topics) >= 2 or get_overall_progress(enr.student_id) < WEAK_THRESHOLD,
                "enrolled_at": enr.enrolled_at.isoformat() if enr.enrolled_at else None,
            }
        )
    return roster


def get_student_report(student_id: int, teacher_id: int, class_id: int) -> dict:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    if not any(e.student_id == student_id for e in list_class_students(class_id)):
        raise LMSNotFoundError("Student not in class")
    user = db.query(DBUser).filter(DBUser.id == student_id).first()
    mastery = get_student_mastery(student_id)
    attempts = (
        db.query(AssessmentAttempt)
        .filter(AssessmentAttempt.student_id == student_id, AssessmentAttempt.status == "submitted")
        .order_by(AssessmentAttempt.submitted_at.desc())
        .limit(20)
        .all()
    )
    return {
        "student_id": student_id,
        "username": user.username if user else None,
        "overall_progress": get_overall_progress(student_id),
        "topics": mastery,
        "recent_attempts": [
            {
                "attempt_id": a.id,
                "assessment_id": a.assessment_id,
                "score_percent": round(100.0 * (a.score or 0) / (a.max_score or 1), 1),
                "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            }
            for a in attempts
        ],
    }


def get_struggling_students(class_id: int, teacher_id: int, threshold: float = WEAK_THRESHOLD) -> List[dict]:
    roster = get_class_roster_summary(class_id, teacher_id)
    return [r for r in roster if r.get("is_struggling") or r.get("overall_progress", 100) < threshold]


def get_progress_over_time(student_id: int) -> List[dict]:
    db = get_db()
    snaps = (
        db.query(MasterySnapshot)
        .filter(MasterySnapshot.student_id == student_id)
        .order_by(MasterySnapshot.created_at)
        .limit(50)
        .all()
    )
    import json

    return [
        {"created_at": s.created_at.isoformat(), "snapshot": json.loads(s.snapshot_json)}
        for s in snaps
    ]


def pdf_source_analytics(teacher_id: int) -> dict:
    db = get_db()
    pdf_qs = (
        db.query(Question)
        .filter(Question.created_by == teacher_id, Question.source_type == "pdf_qa_auto")
        .count()
    )
    manual_qs = (
        db.query(Question)
        .filter(Question.created_by == teacher_id, Question.source_type == "manual")
        .count()
    )
    pdf_quizzes = (
        db.query(Assessment)
        .filter(Assessment.created_by == teacher_id, Assessment.creation_mode == "pdf_qa_auto")
        .count()
    )
    manual_quizzes = (
        db.query(Assessment)
        .filter(Assessment.created_by == teacher_id, Assessment.creation_mode == "manual")
        .count()
    )
    return {
        "pdf_questions": pdf_qs,
        "manual_questions": manual_qs,
        "pdf_quizzes": pdf_quizzes,
        "manual_quizzes": manual_quizzes,
    }


def export_class_csv(class_id: int, teacher_id: int) -> str:
    roster = get_class_roster_summary(class_id, teacher_id)
    lines = ["student_id,username,overall_progress,weak_topics,struggling"]
    for r in roster:
        lines.append(
            f"{r['student_id']},{r.get('username','')},{r.get('overall_progress',0)},"
            f"{r.get('weak_topic_count',0)},{r.get('is_struggling',False)}"
        )
    return "\n".join(lines)
