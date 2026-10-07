#!/usr/bin/env python3
"""
Fresh-reset DIL Saathi roster on dil.iqbalai.com:
  1) Wipe LMS operational data + all non-admin users (admin kept)
  2) Register students/teachers from dil_saathi_users_2026_10_06.json
  3) Create one 9th-grade class per school and enroll students

Run inside flask_app1:
  docker compose exec -T flask_app1 python _qa_audit_tmp/fresh_import_dil_saathi.py
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import text

from app import create_app
from app.models.database_models import User as DBUser
from app.models.models import UserModel
from app.services.lms import class_service
from app.services.lms.exceptions import LMSValidationError
from app.utils.db import get_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent
USERS_PATH = BASE / "dil_saathi_users_2026_10_06.json"
MAPPING_PATH = BASE / "dil_saathi_class_mapping_2026_10_06.json"
GRADE = "9"

# Truncate LMS / user-owned operational tables. Curriculum tables (topics etc.) kept.
WIPE_TABLES = [
    "tutor_chat_messages",
    "tutor_chat_sessions",
    "diagnostic_retake_requests",
    "deficiency_chat_sessions",
    "practice_attempts",
    "practice_sessions",
    "assignment_submissions",
    "learning_path_items",
    "learning_paths",
    "mastery_snapshots",
    "student_topic_scores",
    "attempt_answers",
    "assessment_attempts",
    "assessment_questions",
    "assignments",
    "lesson_assignments",
    "quiz_pdf_sources",
    "diagnostic_target_pdfs",
    "pdf_qa_extractions",
    "assessments",
    "class_enrollments",
    "classes",
    "student_profiles",
    "lesson_topics",
    "lesson_faq",
    "lesson_chat_history",
    "lessons",
]


def wipe_old_data() -> dict:
    db = get_db()
    truncated = []
    skipped = []
    for table in WIPE_TABLES:
        try:
            db.execute(text(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE'))
            truncated.append(table)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            skipped.append((table, str(exc).split("\n")[0][:120]))
            continue
    db.commit()

    deleted = (
        db.query(DBUser)
        .filter(DBUser.role != "admin")
        .delete(synchronize_session=False)
    )
    db.commit()

    # Extra cleanup of other user-owned rows that may still reference deleted users
    extra = [
        "chat_history",
        "conversations",
        "survey_responses",
        "user_prompts",
        "user_documents",
        "user_token_usage",
        "token_reset_history",
        "rag_chunks",
        "rag_threads",
        "rag_prompts",
        "email_verification_tokens",
        "password_reset_tokens",
        "user_settings",
        "llm_usage_events",
    ]
    for table in extra:
        try:
            db.execute(text(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE'))
            truncated.append(table)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            skipped.append((table, str(exc).split("\n")[0][:120]))

    admins = db.query(DBUser).filter(DBUser.role == "admin").count()
    others = db.query(DBUser).filter(DBUser.role != "admin").count()
    logger.info(
        "Wipe done: truncated=%d skipped=%d deleted_non_admin=%d admins_left=%d others_left=%d",
        len(truncated),
        len(skipped),
        deleted,
        admins,
        others,
    )
    for table, msg in skipped:
        logger.warning("skip truncate %s: %s", table, msg)
    return {
        "truncated": truncated,
        "skipped": skipped,
        "deleted_non_admin": deleted,
        "admins_left": admins,
        "others_left": others,
    }


def import_users(records: list[dict]) -> dict:
    created, errors = [], []
    for r in records:
        email = r["email"]
        try:
            existing = UserModel.get_user_by_email(email)
            if existing:
                errors.append((email, "already exists after wipe"))
                continue
            uid = UserModel.create_user(
                username=r["username"],
                useremail=email,
                password=r["password"],
                class_standard=r.get("class_standard") or "9th",
                medium=r.get("medium") or "English",
                groq_api_key="",
                role=r["role"],
                full_name=r.get("full_name") or r["username"],
            )
            if r["role"] == "student":
                try:
                    class_service.set_user_grade(uid, GRADE, role="student")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("grade set failed for %s: %s", email, exc)
            created.append((email, r["role"], uid))
        except Exception as exc:  # noqa: BLE001
            errors.append((email, str(exc)))
            logger.warning("create failed %s: %s", email, exc)
    logger.info(
        "Created %d users (students=%d teachers=%d) errors=%d",
        len(created),
        sum(1 for _, role, _ in created if role == "student"),
        sum(1 for _, role, _ in created if role == "teacher"),
        len(errors),
    )
    return {"created": created, "errors": errors}


def _user_id_by_email(email: str) -> int | None:
    db = get_db()
    user = db.query(DBUser).filter(DBUser.useremail == email).first()
    return user.id if user else None


def create_classes_and_enroll(mapping: list[dict]) -> list[dict]:
    results = []
    for group in mapping:
        school = group["school"]
        teacher_email = group["teacher_email"]
        teacher_name = group.get("teacher_name") or teacher_email
        class_name = f"9th Grade Mathematics - {school}"
        teacher_id = _user_id_by_email(teacher_email) if teacher_email else None
        if not teacher_id:
            logger.error("Teacher missing for %s: %s", school, teacher_email)
            results.append(
                {
                    "school": school,
                    "error": f"teacher not found: {teacher_email}",
                    "enrolled_ok": 0,
                    "students_targeted": len(group.get("student_emails") or []),
                }
            )
            continue

        school_class = class_service.create_class(
            teacher_id=teacher_id,
            name=class_name,
            description=f"DIL Saathi 9th Grade — {school} (fresh import 2026-10-06)",
            grade_level=GRADE,
        )
        enrolled, errors = 0, []
        for student_email in group.get("student_emails") or []:
            student_id = _user_id_by_email(student_email)
            if not student_id:
                errors.append((student_email, "user not found"))
                continue
            try:
                class_service.enroll_student(school_class.join_code, student_id)
                enrolled += 1
            except LMSValidationError as exc:
                errors.append((student_email, str(exc)))
        results.append(
            {
                "school": school,
                "teacher": teacher_name,
                "teacher_email": teacher_email,
                "class_name": class_name,
                "join_code": school_class.join_code,
                "students_targeted": len(group.get("student_emails") or []),
                "enrolled_ok": enrolled,
                "errors": errors,
            }
        )
        logger.info(
            "%s join=%s enrolled=%d/%d",
            school,
            school_class.join_code,
            enrolled,
            len(group.get("student_emails") or []),
        )
    return results


def main() -> int:
    records = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    logger.info("Loaded %d users, %d school groups", len(records), len(mapping))

    wipe = wipe_old_data()
    if wipe["others_left"] != 0:
        logger.error("Non-admin users still present after wipe: %s", wipe["others_left"])
        return 2

    imported = import_users(records)
    class_results = create_classes_and_enroll(mapping)

    db = get_db()
    counts = {
        role: db.query(DBUser).filter(DBUser.role == role).count()
        for role in ("admin", "teacher", "student")
    }
    print("\n=== FINAL COUNTS ===")
    print(counts)
    print("\n=== CLASSES ===")
    for r in class_results:
        if r.get("error"):
            print(f"FAIL {r['school']}: {r['error']}")
            continue
        print(
            f"{r['school']:<15} | {r['teacher']:<20} | join={r['join_code']} "
            f"| enrolled {r['enrolled_ok']}/{r['students_targeted']}"
        )
        for email, msg in r.get("errors") or []:
            print(f"    ERROR {email}: {msg}")

    ok = (
        len(imported["errors"]) == 0
        and all(r.get("enrolled_ok", 0) == r.get("students_targeted", -1) for r in class_results)
        and counts["student"] == sum(1 for r in records if r["role"] == "student")
        and counts["teacher"] == sum(1 for r in records if r["role"] == "teacher")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        raise SystemExit(main())
