"""Create a FRESH throwaway grade-8 student for the student-UI E2E suite (LOCAL DB ONLY).

The diagnostic is one-time per account, so every run needs a new student:

    python _qa_audit_tmp/student_ui_e2e/seed_student.py <suffix> [grade] [diag-done|admin]   -> prints the email

Only grade 7 has a published diagnostic locally, while the e2e teacher's classes/lessons are grade 8.
`diag-done` marks the diagnostic complete (StudentProfile) so a grade-8 student can reach the gated
Classes / AI Tutor screens without a grade-8 diagnostic.

Same fields a normal signup writes (see teacher_ui_e2e/seed_users.py). Refuses to run unless ENV=local
and DATABASE_URL is SQLite.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import Config  # noqa: E402
from app.models.database_models import User  # noqa: E402

PASSWORD = os.environ.get("E2E_PASSWORD", "E2eTeacher!2026")


def main() -> int:
    suffix = sys.argv[1] if len(sys.argv) > 1 else "manual"
    grade = sys.argv[2] if len(sys.argv) > 2 else "8"
    diag_done = len(sys.argv) > 3 and sys.argv[3] == "diag-done"
    role = "admin" if len(sys.argv) > 3 and sys.argv[3] == "admin" else "student"
    uri = Config.SQLALCHEMY_DATABASE_URI
    if os.environ.get("ENV", "local") != "local" or not str(uri).startswith("sqlite"):
        print(f"Refusing to seed: ENV={os.environ.get('ENV')} DB={uri}", file=sys.stderr)
        return 1
    username = f"e2e_stu_{suffix}"
    email = f"e2e.stu.{suffix}@iqbalai.local"
    session = sessionmaker(bind=create_engine(uri))()
    try:
        if not session.query(User).filter(User.useremail == email).first():
            session.add(User(
                username=username, useremail=email, password=PASSWORD, role=role,
                class_standard=grade, medium="English", groq_api_key="", subscription_tier="free",
            ))
            session.commit()
        if diag_done:
            from app.models.lms_models import StudentProfile
            from datetime import datetime
            uid = session.query(User).filter(User.useremail == email).first().id
            prof = session.query(StudentProfile).filter(StudentProfile.user_id == uid).first()
            if not prof:
                prof = StudentProfile(user_id=uid)
                session.add(prof)
            prof.diagnostic_completed = True
            prof.diagnostic_completed_at = datetime.utcnow()
            session.commit()
        print(email)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
