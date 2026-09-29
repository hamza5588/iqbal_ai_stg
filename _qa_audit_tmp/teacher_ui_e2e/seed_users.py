"""Seed throwaway accounts for the teacher-UI E2E suite (LOCAL DB ONLY).

Creates (idempotently) one teacher and three grade-8 students using the same
fields a normal signup writes (see _qa_audit_tmp/import_dil_saathi_users.py).
Refuses to run unless ENV=local and DATABASE_URL is SQLite.
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
ACCOUNTS = [
    ("e2e_teacher", "e2e.teacher@iqbalai.local", "teacher", "8"),
    ("e2e_student_a", "e2e.student.a@iqbalai.local", "student", "8"),
    ("e2e_student_b", "e2e.student.b@iqbalai.local", "student", "8"),
    ("e2e_student_c", "e2e.student.c@iqbalai.local", "student", "8"),
]


def main() -> int:
    uri = Config.SQLALCHEMY_DATABASE_URI
    if os.environ.get("ENV", "local") != "local" or not str(uri).startswith("sqlite"):
        print(f"Refusing to seed: ENV={os.environ.get('ENV')} DB={uri}")
        return 1
    engine = create_engine(uri)
    session = sessionmaker(bind=engine)()
    try:
        for username, email, role, grade in ACCOUNTS:
            if session.query(User).filter(User.useremail == email).first():
                print("exists", email)
                continue
            session.add(User(
                username=username, useremail=email, password=PASSWORD, role=role,
                class_standard=grade, medium="English", groq_api_key="", subscription_tier="free",
            ))
            session.commit()
            print("created", email, role)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
