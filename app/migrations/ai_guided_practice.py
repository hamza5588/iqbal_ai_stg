"""AI Guided Practice: link pool questions to admin-approved Target-bank questions.

Additive and idempotent: ``lms_aigp_questions.bank_question_id`` (nullable) is added
only when missing. Existing rows are untouched (NULL = AI-generated question).

Rollback: ignore the nullable column.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)

NEW_COLUMNS = (("lms_aigp_questions", "bank_question_id", "INTEGER"),)


def run_migration(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, column, ddl in NEW_COLUMNS:
        if table not in tables:
            continue  # create_all creates the table with the column
        if column in {c["name"] for c in inspector.get_columns(table)}:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        logger.info("%s.%s added", table, column)
