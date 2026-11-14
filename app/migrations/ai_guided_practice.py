"""AI Guided Practice additive migrations (idempotent).

* ``lms_aigp_questions.bank_question_id`` - link to admin-approved Target-bank questions.
* ``lms_aigp_session_items.wrong_options_json`` / ``attempt_count`` / ``solved_at`` -
  Learning-Chat style answering: wrong picks stay on the question until it is solved.
  Backfill: items answered correctly under the old one-shot flow count as solved.

Existing rows are otherwise untouched. Rollback: ignore the nullable columns.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)

NEW_COLUMNS = (
    ("lms_aigp_questions", "bank_question_id", "INTEGER"),
    ("lms_aigp_session_items", "wrong_options_json", "TEXT"),
    ("lms_aigp_session_items", "attempt_count", "INTEGER NOT NULL DEFAULT 0"),
    ("lms_aigp_session_items", "solved_at", "TIMESTAMP"),
)


def run_migration(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    added = set()
    for table, column, ddl in NEW_COLUMNS:
        if table not in tables:
            continue  # create_all creates the table with the column
        if column in {c["name"] for c in inspector.get_columns(table)}:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        added.add((table, column))
        logger.info("%s.%s added", table, column)
    if ("lms_aigp_session_items", "solved_at") in added:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE lms_aigp_session_items SET solved_at = answered_at, attempt_count = 1 "
                    "WHERE answered_at IS NOT NULL AND is_correct = :yes AND solved_at IS NULL"
                ),
                {"yes": True},
            )
            conn.execute(
                text(
                    "UPDATE lms_aigp_session_items SET attempt_count = 1, "
                    "wrong_options_json = '[' || CAST(selected_original_index AS TEXT) || ']' "
                    "WHERE answered_at IS NOT NULL AND attempt_count = 0 AND selected_original_index IS NOT NULL"
                )
            )
        logger.info("lms_aigp_session_items: backfilled solved_at for correctly answered items")
