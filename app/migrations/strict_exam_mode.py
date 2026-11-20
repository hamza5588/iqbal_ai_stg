"""Strict Exam Mode: locked attempt status, lock fields, time extensions."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.models.database_models import Base
from app.models import lms_models  # noqa: F401

logger = logging.getLogger(__name__)

ATTEMPT_COLS = {
    "locked_at": "TIMESTAMP",
    "lock_reason": "VARCHAR(128)",
    "lock_event_id": "INTEGER",
    "current_question_index": "INTEGER",
}


def _widen_attempt_status_check(conn, dialect: str) -> None:
    if dialect == "postgresql":
        conn.execute(text("ALTER TABLE assessment_attempts DROP CONSTRAINT IF EXISTS check_attempt_status"))
        conn.execute(
            text(
                "ALTER TABLE assessment_attempts ADD CONSTRAINT check_attempt_status "
                "CHECK (status IN ('in_progress','locked','submitted','abandoned'))"
            )
        )
        conn.execute(
            text("ALTER TABLE assessments DROP CONSTRAINT IF EXISTS check_assessment_security_mode")
        )
        conn.execute(
            text(
                "ALTER TABLE assessments ADD CONSTRAINT check_assessment_security_mode "
                "CHECK (security_mode IN ('standard','seb','supervised','strict'))"
            )
        )
        return
    # SQLite: CHECK is baked into table DDL; new create_all / fresh DBs pick up model.
    # Existing SQLite files: status='locked' may still work if PRAGMA ignore, else
    # production uses Postgres. Log only.
    logger.info("strict_exam_mode: skipped CHECK rewrite on dialect=%s", dialect)


def run_migration(engine) -> None:
    Base.metadata.tables["attempt_time_extensions"].create(bind=engine, checkfirst=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if "assessment_attempts" in tables:
            existing = {c["name"] for c in inspector.get_columns("assessment_attempts")}
            for name, ddl in ATTEMPT_COLS.items():
                if name not in existing:
                    logger.info("Adding assessment_attempts.%s", name)
                    conn.execute(text(f"ALTER TABLE assessment_attempts ADD COLUMN {name} {ddl}"))
            try:
                _widen_attempt_status_check(conn, dialect)
            except Exception as exc:  # noqa: BLE001
                logger.warning("strict status check widen: %s", exc)
