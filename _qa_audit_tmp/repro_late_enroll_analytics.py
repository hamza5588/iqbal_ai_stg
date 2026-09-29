#!/usr/bin/env python3
"""
Reproduce & diagnose: a student completes the diagnostic BEFORE being
enrolled in a teacher's class - does the teacher's Class Analytics /
Struggling Students view show the student's real marks, or 0%?

Creates a throwaway teacher + student, has the student take the platform
diagnostic (answering a known, deliberate mix of correct/incorrect so we
know the ground-truth score), THEN creates the teacher's class and enrolls
the student (reproducing "enrolled after assessment"), then compares:
  - the raw attempt score (ground truth)
  - performance_service.get_overall_progress / get_student_mastery (direct)
  - analytics_service.get_class_roster_summary (what the teacher sees)

Run from inside the flask_app1 container:
    docker compose exec flask_app1 python _qa_audit_tmp/repro_late_enroll_analytics.py
"""
from __future__ import annotations

import logging

from app import create_app
from app.models.database_models import User as DBUser
from app.services.lms import analytics_service, assessment_service, attempt_service, class_service, performance_service
from app.utils.db import get_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TEACHER_EMAIL = "repro.teacher.lateenroll@test.local"
STUDENT_EMAIL = "repro.student.lateenroll@test.local"


def _get_or_create_user(email, username, role, class_standard):
    db = get_db()
    user = db.query(DBUser).filter(DBUser.useremail == email).first()
    if user:
        return user
    user = DBUser(
        username=username, useremail=email, password="repro-pass-1234",
        role=role, class_standard=class_standard, medium="English",
        groq_api_key="", subscription_tier="free",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def main() -> int:
    teacher = _get_or_create_user(TEACHER_EMAIL, "repro_teacher_lateenroll", "teacher", "")
    student = _get_or_create_user(STUDENT_EMAIL, "repro_student_lateenroll", "student", "9")
    logger.info("teacher_id=%s student_id=%s", teacher.id, student.id)

    diagnostic = assessment_service.get_active_platform_diagnostic()
    if not diagnostic:
        logger.error("No active platform diagnostic - cannot reproduce.")
        return 1
    logger.info("Using diagnostic assessment_id=%s (%s questions)", diagnostic.id, len(diagnostic.questions))

    # --- Step 1: student takes the diagnostic BEFORE any class exists ---
    attempt, resumed = attempt_service.start_attempt(student.id, diagnostic.id)
    logger.info("Started attempt_id=%s (resumed=%s)", attempt.id, resumed)

    from app.models.lms_models import Question
    db = get_db()
    q_ids = [aq.question_id for aq in diagnostic.questions]
    questions = db.query(Question).filter(Question.id.in_(q_ids)).all()

    # Deliberate, known ground truth: answer correctly except every 3rd
    # question - so we know exactly what the "real marks" should be.
    n_correct = 0
    for i, q in enumerate(questions):
        if i % 3 == 0:
            wrong_idx = (q.correct_option_index + 1) % 4
            attempt_service.save_answer(attempt.id, q.id, wrong_idx)
        else:
            attempt_service.save_answer(attempt.id, q.id, q.correct_option_index)
            n_correct += 1
    expected_pct = round(100.0 * n_correct / len(questions), 2) if questions else 0.0
    logger.info("Deliberately answered %d/%d correctly (expected %.2f%%)", n_correct, len(questions), expected_pct)

    result = attempt_service.submit_attempt(attempt.id)
    logger.info("Raw attempt result: score=%s/%s (%.2f%%)", result["score"], result["max_score"], result["score_percent"])

    # Ground truth, straight from performance_service, BEFORE any class link.
    progress_before = performance_service.get_overall_progress(student.id)
    mastery_before = performance_service.get_student_mastery(student.id)
    logger.info("get_overall_progress (pre-enroll) = %s", progress_before)
    logger.info("get_student_mastery (pre-enroll) = %s", mastery_before)

    # --- Step 2: teacher creates a class and enrolls the student AFTER ---
    class_name = "Repro 9th - Late Enroll Test"
    existing = [c for c in class_service.list_teacher_classes(teacher.id) if c.name == class_name]
    school_class = existing[0] if existing else class_service.create_class(
        teacher_id=teacher.id, name=class_name, grade_level="9",
    )
    logger.info("Class id=%s join_code=%s", school_class.id, school_class.join_code)
    class_service.enroll_student(school_class.join_code, student.id)
    logger.info("Enrolled student %s into class %s AFTER diagnostic submission", student.id, school_class.id)

    # --- Step 3: what does the teacher's analytics actually show now? ---
    roster = analytics_service.get_class_roster_summary(school_class.id, teacher.id)
    row = next((r for r in roster if r["student_id"] == student.id), None)
    logger.info("Teacher roster row for this student: %s", row)

    print("\n=== COMPARISON ===")
    print(f"Ground-truth diagnostic score:      {result['score_percent']}%")
    print(f"performance_service overall progress: {progress_before}%")
    print(f"analytics_service roster overall_progress: {row.get('overall_progress') if row else 'STUDENT NOT IN ROSTER'}%")
    print(f"analytics_service roster weak_topics: {row.get('weak_topics') if row else None}")

    if row and abs((row.get("overall_progress") or 0) - progress_before) > 0.01:
        print("\n*** DISCREPANCY CONFIRMED: analytics roster differs from performance_service ground truth ***")
    elif row and abs(progress_before - result["score_percent"]) > 0.01:
        print("\n*** progress_before also differs from raw attempt score - discrepancy is upstream of analytics ***")
    else:
        print("\nNo discrepancy reproduced - roster matches ground truth.")

    return 0


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        raise SystemExit(main())
