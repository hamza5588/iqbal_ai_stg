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


def find_active_assignment_for_class_quiz(
    class_id: int, quiz_id: int, *, exclude_assignment_id: Optional[int] = None
) -> Optional[Assignment]:
    """Published (or draft) assignment already linking this quiz to this class."""
    db = get_db()
    q = db.query(Assignment).filter(
        Assignment.class_id == class_id,
        Assignment.quiz_id == quiz_id,
        Assignment.status.in_(("draft", "published")),
    )
    if exclude_assignment_id is not None:
        q = q.filter(Assignment.id != exclude_assignment_id)
    return q.order_by(Assignment.id.asc()).first()


def create_assignment(
    teacher_id: int,
    class_id: int,
    quiz_id: int,
    title: str,
    instructions: Optional[str] = None,
    due_date: Optional[datetime] = None,
) -> Assignment:
    title = title.strip() if isinstance(title, str) else ""
    if not title:
        raise LMSValidationError("Assignment title is required")
    quiz = get_assessment(quiz_id)
    if quiz.assessment_type != "quiz":
        raise LMSValidationError("Assignment must reference a quiz assessment")
    if quiz.created_by != teacher_id:
        raise LMSValidationError("You can only assign your own quizzes")
    if quiz.status != "published":
        raise LMSValidationError("Publish the quiz before assigning it to a class")
    school_class = get_class_by_id(class_id)
    if school_class.teacher_id != teacher_id:
        raise LMSValidationError("Teacher does not own this class")

    # Same quiz → same class twice creates duplicate student rows and false
    # "already attempted" confusion when the student finished the first one.
    existing = find_active_assignment_for_class_quiz(class_id, quiz_id)
    if existing:
        raise LMSValidationError(
            "You have already assigned this quiz to this class. "
            "Please create a different quiz and assign that, or choose another class."
        )

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

    dup = find_active_assignment_for_class_quiz(
        assignment.class_id,
        assignment.quiz_id,
        exclude_assignment_id=assignment.id,
    )
    if dup and dup.status == "published":
        raise LMSValidationError(
            "You have already assigned this quiz to this class. "
            "Please create a different quiz and assign that, or choose another class."
        )

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
    """Active assignments for a class (draft + published). Closed ones are omitted."""
    db = get_db()
    return (
        db.query(Assignment)
        .filter(
            Assignment.class_id == class_id,
            Assignment.status.in_(("draft", "published")),
        )
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
        .order_by(Assignment.id.asc())
        .all()
    )
    # Build per-assignment rows first.
    rows = []
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
        # If this quiz was already submitted under ANY assignment for this class,
        # mark later duplicates as completed so Start doesn't open a dead end.
        already_done_same_quiz = False
        if status != "submitted":
            from app.models.lms_models import AssessmentAttempt

            already_done_same_quiz = (
                db.query(AssessmentAttempt.id)
                .filter(
                    AssessmentAttempt.student_id == student_id,
                    AssessmentAttempt.assessment_id == assignment.quiz_id,
                    AssessmentAttempt.status == "submitted",
                )
                .first()
                is not None
            )
            if already_done_same_quiz:
                status = "submitted"
        rows.append(
            {
                "assignment_id": assignment.id,
                "class_id": assignment.class_id,
                "attempt_id": sub.attempt_id if sub else None,
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

    # Collapse duplicate (class, quiz) rows from older re-assigns — keep one.
    # Prefer a row the student already worked on; else the oldest assignment.
    rank = {"submitted": 0, "in_progress": 1, "not_started": 2}
    best: dict = {}
    for row in rows:
        key = (row["class_id"], row["quiz_id"])
        cur = best.get(key)
        if cur is None:
            best[key] = row
            continue
        r_new = rank.get(row["status"], 9)
        r_old = rank.get(cur["status"], 9)
        if r_new < r_old or (
            r_new == r_old and row["assignment_id"] < cur["assignment_id"]
        ):
            # Keep attempt_id from the worked row when collapsing
            if not row.get("attempt_id") and cur.get("attempt_id"):
                row = dict(row)
                row["attempt_id"] = cur["attempt_id"]
                row["score_percent"] = cur.get("score_percent")
                row["submitted_at"] = cur.get("submitted_at")
            best[key] = row
        elif cur.get("attempt_id") is None and row.get("attempt_id"):
            cur = dict(cur)
            cur["attempt_id"] = row["attempt_id"]
            cur["score_percent"] = row.get("score_percent")
            cur["submitted_at"] = row.get("submitted_at")
            if rank.get(row["status"], 9) < rank.get(cur["status"], 9):
                cur["status"] = row["status"]
                cur["can_start"] = row["can_start"]
            best[key] = cur

    result = list(best.values())
    result.sort(
        key=lambda r: (
            r.get("due_date") or "9999",
            r.get("assignment_id") or 0,
        )
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


def student_is_assigned_quiz(student_id: int, quiz_id: int) -> bool:
    """True if the student joined a class that has this published quiz assigned."""
    class_ids = [c.id for c in list_student_classes(student_id)]
    if not class_ids:
        return False
    db = get_db()
    row = (
        db.query(Assignment)
        .filter(
            Assignment.quiz_id == quiz_id,
            Assignment.class_id.in_(class_ids),
            Assignment.status == "published",
        )
        .first()
    )
    return row is not None


def resolve_student_quiz_assignment(
    student_id: int,
    quiz_id: int,
    assignment_id: Optional[int] = None,
) -> int:
    """Return the assignment id this student may use for the quiz."""
    class_ids = [c.id for c in list_student_classes(student_id)]
    if not class_ids:
        raise LMSValidationError(
            "Join your teacher's class with a class code to take this quiz."
        )
    if assignment_id is not None:
        assignment = get_assignment(int(assignment_id))
        if assignment.quiz_id != quiz_id:
            raise LMSValidationError("This assignment does not match the quiz")
        if assignment.status != "published":
            raise LMSValidationError("This assignment is not published")
        if assignment.class_id not in class_ids:
            raise LMSValidationError(
                "Join your teacher's class with a class code to take this quiz."
            )
        return assignment.id
    db = get_db()
    rows = (
        db.query(Assignment)
        .filter(
            Assignment.quiz_id == quiz_id,
            Assignment.class_id.in_(class_ids),
            Assignment.status == "published",
        )
        .all()
    )
    if not rows:
        raise LMSValidationError(
            "Join your teacher's class with a class code to take this quiz."
        )
    return rows[0].id
