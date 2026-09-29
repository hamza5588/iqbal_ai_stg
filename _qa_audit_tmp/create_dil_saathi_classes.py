#!/usr/bin/env python3
"""
Create one 9th-grade LMS class per DIL Saathi teacher (from
dil_saathi_class_mapping.json) and enroll their corresponding students,
using the app's own service layer (app.services.lms.class_service) so join
codes, grade validation, and enrollment records are created exactly the way
the product itself creates them.

Idempotent: reuses an existing class (same teacher + name) instead of making
a duplicate on re-run; enroll_student() itself no-ops on an existing active
enrollment.

Run from inside the flask_app1 container (needs an app context for get_db()):
    docker compose exec flask_app1 python _qa_audit_tmp/create_dil_saathi_classes.py
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app import create_app
from app.models.database_models import User as DBUser
from app.services.lms import class_service
from app.services.lms.exceptions import LMSValidationError
from app.utils.db import get_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent / "dil_saathi_class_mapping.json"
GRADE = "9"


def _user_id_by_email(email: str) -> int | None:
    db = get_db()
    user = db.query(DBUser).filter(DBUser.useremail == email).first()
    return user.id if user else None


def main() -> int:
    mapping = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    logger.info("Loaded %d school/teacher groups", len(mapping))

    results = []
    for group in mapping:
        school = group["school"]
        teacher_email = group["teacher_email"]
        teacher_name = group["teacher_name"]
        class_name = f"9th Grade Mathematics - {school}"

        teacher_id = _user_id_by_email(teacher_email)
        if not teacher_id:
            logger.error("Teacher not found: %s (%s)", teacher_email, teacher_name)
            continue

        existing = [c for c in class_service.list_teacher_classes(teacher_id) if c.name == class_name]
        if existing:
            school_class = existing[0]
            logger.info("Reusing existing class '%s' (join_code=%s)", class_name, school_class.join_code)
        else:
            school_class = class_service.create_class(
                teacher_id=teacher_id,
                name=class_name,
                description=f"DIL Saathi 9th Grade — {school} (auto-created from roster import)",
                grade_level=GRADE,
            )
            logger.info("Created class '%s' (join_code=%s)", class_name, school_class.join_code)

        enrolled, skipped, errors = 0, 0, []
        for student_email in group["student_emails"]:
            student_id = _user_id_by_email(student_email)
            if not student_id:
                errors.append((student_email, "user not found"))
                continue
            try:
                class_service.enroll_student(school_class.join_code, student_id)
                enrolled += 1
            except LMSValidationError as exc:
                errors.append((student_email, str(exc)))

        results.append({
            "school": school,
            "teacher": teacher_name,
            "teacher_email": teacher_email,
            "class_name": class_name,
            "join_code": school_class.join_code,
            "students_targeted": len(group["student_emails"]),
            "enrolled_ok": enrolled,
            "errors": errors,
        })

    print("\n=== Summary ===")
    for r in results:
        print(f"{r['school']:<15} | teacher={r['teacher']:<20} | join_code={r['join_code']} "
              f"| enrolled {r['enrolled_ok']}/{r['students_targeted']}")
        for email, msg in r["errors"]:
            print(f"    ERROR {email}: {msg}")

    total_targeted = sum(r["students_targeted"] for r in results)
    total_enrolled = sum(r["enrolled_ok"] for r in results)
    print(f"\nTotal: {total_enrolled}/{total_targeted} students enrolled across {len(results)} classes")

    return 0 if total_enrolled == total_targeted else 1


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        raise SystemExit(main())
