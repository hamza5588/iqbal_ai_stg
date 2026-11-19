"""Additive exam security columns + events table."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.models.database_models import Base
from app.models import lms_models  # noqa: F401

logger = logging.getLogger(__name__)

ASSESSMENT_COLS = {
    "security_mode": "VARCHAR(32) NOT NULL DEFAULT 'standard'",
    "require_seb": "BOOLEAN NOT NULL DEFAULT FALSE",
    "allow_resume": "BOOLEAN NOT NULL DEFAULT TRUE",
    "max_interruptions": "INTEGER",
    "max_concurrent_sessions": "INTEGER NOT NULL DEFAULT 1",
    "exam_window_start": "TIMESTAMP",
    "exam_window_end": "TIMESTAMP",
    "auto_submit_on_expiry": "BOOLEAN NOT NULL DEFAULT TRUE",
    "warn_on_focus_loss": "BOOLEAN NOT NULL DEFAULT TRUE",
    "seb_config_key_hash": "VARCHAR(128)",
    "seb_browser_exam_keys_json": "TEXT",
    "seb_quit_password_hash": "VARCHAR(128)",
    "seb_quit_link_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    "security_policy_json": "TEXT",
}

ATTEMPT_COLS = {
    "session_token": "VARCHAR(64)",
    "security_mode": "VARCHAR(32)",
    "seb_verified": "BOOLEAN NOT NULL DEFAULT FALSE",
    "interruption_count": "INTEGER NOT NULL DEFAULT 0",
    "focus_loss_count": "INTEGER NOT NULL DEFAULT 0",
    "client_fingerprint": "VARCHAR(128)",
    "last_security_event_at": "TIMESTAMP",
    "finalized_reason": "VARCHAR(64)",
}


def run_migration(engine) -> None:
    Base.metadata.tables["exam_security_events"].create(bind=engine, checkfirst=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "assessments" in tables:
            existing = {c["name"] for c in inspector.get_columns("assessments")}
            for name, ddl in ASSESSMENT_COLS.items():
                if name not in existing:
                    logger.info("Adding assessments.%s", name)
                    conn.execute(text(f"ALTER TABLE assessments ADD COLUMN {name} {ddl}"))
        if "assessment_attempts" in tables:
            existing = {c["name"] for c in inspector.get_columns("assessment_attempts")}
            for name, ddl in ATTEMPT_COLS.items():
                if name not in existing:
                    logger.info("Adding assessment_attempts.%s", name)
                    conn.execute(text(f"ALTER TABLE assessment_attempts ADD COLUMN {name} {ddl}"))
            # Index for session token lookups (ignore if exists)
            try:
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_assessment_attempts_session_token "
                        "ON assessment_attempts (session_token)"
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("session_token index warning: %s", exc)
