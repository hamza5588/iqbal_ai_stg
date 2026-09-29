#!/usr/bin/env python3
"""
Bulk-import DIL Saathi student/teacher accounts from dil_saathi_users.json
into the `users` table, using the exact same fields/conventions as a normal
signup through UserModel.create_user() (app/models/models.py):
  - password stored as-is (this app does not hash passwords - matching the
    existing login check `user['password'] == request.form['password']`)
  - role in ('student','teacher'), class_standard/medium/groq_api_key as the
    register form would set them
  - subscription_tier='free'

Idempotent: skips (does not touch) any row whose useremail already exists.
Run from inside the flask_app1 container so Config/DATABASE_URL resolve the
same way the app does:
    docker compose exec flask_app1 python _qa_audit_tmp/import_dil_saathi_users.py
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Config
from app.models.database_models import User

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent / "dil_saathi_users.json"


def main() -> int:
    records = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    logger.info("Loaded %d records from %s", len(records), DATA_PATH)

    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, **Config.SQLALCHEMY_ENGINE_OPTIONS)
    Session = sessionmaker(bind=engine)
    session = Session()

    created, skipped_existing, errors = [], [], []
    try:
        existing_emails = {
            e for (e,) in session.query(User.useremail).filter(
                User.useremail.in_([r["email"] for r in records])
            ).all()
        }

        for r in records:
            email = r["email"]
            if email in existing_emails:
                skipped_existing.append(email)
                continue
            try:
                user = User(
                    username=r["username"],
                    useremail=email,
                    password=r["password"],
                    role=r["role"],
                    class_standard=r["class_standard"],
                    medium=r["medium"],
                    groq_api_key="",
                    subscription_tier="free",
                )
                session.add(user)
                session.commit()
                created.append((email, r["role"]))
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                errors.append((email, str(exc)))
                logger.warning("Failed to create %s (%s): %s", email, r["role"], exc)
    finally:
        session.close()

    logger.info("Created: %d (students=%d, teachers=%d)",
                len(created),
                sum(1 for _, role in created if role == "student"),
                sum(1 for _, role in created if role == "teacher"))
    logger.info("Skipped (already existed): %d", len(skipped_existing))
    logger.info("Errors: %d", len(errors))
    if errors:
        for email, msg in errors:
            logger.warning("  %s: %s", email, msg)

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
