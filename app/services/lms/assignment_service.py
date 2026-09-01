"""Assignment service (quiz-only assignments)."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from app.models.lms_models import Assignment, AssignmentSubmission
from app.services.lms.assessment_service import get_assessment
from app.services.lms.class_service import (
    get_class_by_id,
    list_class_students,
    list_student_classes,
)
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.utils.db import get_db


def create_assignment(
    teacher_id: int,
    class_id: int,
    quiz_id: int,
    title: str,
    instructions: Optional[str] = None,
    due_date: Optional[datetime] = None,
) -> Assignment:
    quiz = get_assessment(quiz_id)
    if quiz.assessment_type != "quiz":
        raise LMSValidationError("Assignment must reference a quiz assessment")
    school_class = get_class_by_id(class_id)
    if school_class.teacher_id != teacher_id:
        raise LMSValidationError("Teacher does not own this class")

    db = get_db()
    assignment = Assignment(
        teacher_id=teacher_id,
        class_id=class_id,
        quiz_id=quiz_id,
        title=title,
        instructions=instructions,
        due_date=due_date,
        status="draft",
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def publish_assignment(assignment_id: int, teacher_id: int) -> Assignment:
    db = get_db()
    assignment = get_assignment(assignment_id)
    if assignment.teacher_id != teacher_id:
        raise LMSValidationError("Not authorized")
    quiz = get_assessment(assignment.quiz_id)
    if quiz.status != "published":
        raise LMSValidationError("Quiz must be published before assigning")

    assignment.status = "published"
    enrollments = list_class_students(assignment.class_id)
    for enr in enrollments:
        existing = (
            db.query(AssignmentSubmission)
            .filter(
                AssignmentSubmission.assignment_id == assignment_id,
                AssignmentSubmission.student_id == enr.student_id,
            )
            .first()
        )
        if not existing:
            db.add(
                AssignmentSubmission(
                    assignment_id=assignment_id,
                    student_id=enr.student_id,
                    status="not_started",
                )
            )
    db.commit()
    db.refresh(assignment)
    return assignment


def get_assignment(assignment_id: int) -> Assignment:
    db = get_db()
    a = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not a:
        raise LMSNotFoundError(f"Assignment {assignment_id} not found")
    return a


def list_assignments_for_class(class_id: int) -> List[Assignment]:
    db = get_db()
    return (
        db.query(Assignment)
        .filter(Assignment.class_id == class_id, Assignment.status == "published")
        .order_by(Assignment.due_date)
        .all()
    )


def list_submissions_for_assignment(assignment_id: int, teacher_id: int) -> List[dict]:
    db = get_db()
    assignment = get_assignment(assignment_id)
    if assignment.teacher_id != teacher_id:
        raise LMSValidationError("Not authorized")
    from app.models.lms_models import AssessmentAttempt

    subs = (
        db.query(AssignmentSubmission)
        .filter(AssignmentSubmission.assignment_id == assignment_id)
        .all()
    )
    result = []
    for sub in subs:
        score_pct = None
        if sub.attempt_id:
            att = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == sub.attempt_id).first()
            if att and att.max_score:
                score_pct = round(100.0 * (att.score or 0) / att.max_score, 1)
        result.append(
            {
                "student_id": sub.student_id,
                "status": sub.status,
                "attempt_id": sub.attempt_id,
                "score_percent": score_pct,
                "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
            }
        )
    return result


def list_assignments_for_student(student_id: int) -> List[dict]:
    db = get_db()
    class_ids = [c.id for c in list_student_classes(student_id)]
    if not class_ids:
        return []
    assignments = (
        db.query(Assignment)
        .filter(Assignment.class_id.in_(class_ids), Assignment.status == "published")
        .order_by(Assignment.due_date)
        .all()
    )
    result = []
    for assignment in assignments:
        sub = (
            db.query(AssignmentSubmission)
            .filter(
                AssignmentSubmission.assignment_id == assignment.id,
                AssignmentSubmission.student_id == student_id,
            )
            .first()
        )
        status = sub.status if sub else "not_started"
        score_pct = None
        if sub and sub.attempt_id:
            from app.models.lms_models import AssessmentAttempt

            att = (
                db.query(AssessmentAttempt)
                .filter(AssessmentAttempt.id == sub.attempt_id)
                .first()
            )
            if att and att.status == "submitted" and att.max_score:
                score_pct = round(100.0 * (att.score or 0) / att.max_score, 1)
        result.append(
            {
                "assignment_id": assignment.id,
                "title": assignment.title,
                "quiz_id": assignment.quiz_id,
                "due_date": assignment.due_date.isoformat() if assignment.due_date else None,
                "status": status,
                "submitted_at": sub.submitted_at.isoformat()
                if sub and sub.submitted_at
                else None,
                "score_percent": score_pct,
                "can_start": status != "submitted",
            }
        )
    return result


def link_attempt_to_submission(
    assignment_id: int, student_id: int, attempt_id: int
) -> AssignmentSubmission:
    db = get_db()
    sub = (
        db.query(AssignmentSubmission)
        .filter(
            AssignmentSubmission.assignment_id == assignment_id,
            AssignmentSubmission.student_id == student_id,
        )
        .first()
    )
    if not sub:
        sub = AssignmentSubmission(
            assignment_id=assignment_id,
            student_id=student_id,
            status="in_progress",
        )
        db.add(sub)
    sub.attempt_id = attempt_id
    sub.status = "in_progress"
    db.commit()
    db.refresh(sub)
    return sub


def mark_submission_complete(assignment_id: int, student_id: int, attempt_id: int) -> None:
    db = get_db()
    sub = link_attempt_to_submission(assignment_id, student_id, attempt_id)
    sub.status = "submitted"
    sub.submitted_at = datetime.utcnow()
    db.commit()
