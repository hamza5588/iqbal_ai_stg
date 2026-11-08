"""Domain -> Subtopic taxonomy, subtopic mastery and Target PDF question bank tables.

Idempotent: every table is created with ``checkfirst`` and the one new column on
an existing table is only added when missing. Nothing existing is rewritten, so
rolling back is "drop the new tables + ignore the nullable column" (see
docs/SUBTOPIC_LEARNING_SYSTEM.md). Historical rows are backfilled separately by
``scripts/backfill_subtopic_taxonomy.py`` so a deploy never blocks on it.
"""
import logging

from sqlalchemy import inspect, text

from app.models import lms_models  # noqa: F401 - registers the models on Base.metadata
from app.models.database_models import Base

logger = logging.getLogger(__name__)

TABLE_NAMES = (
    "lms_subtopics",
    "lms_question_taxonomy",
    "lms_subtopic_evidence",
    "lms_student_subtopic_scores",
    "lms_target_bank_documents",
    "lms_target_bank_items",
    "lms_guided_practice_attempts",
)


def run_migration(engine) -> None:
    for table_name in TABLE_NAMES:
        Base.metadata.tables[table_name].create(bind=engine, checkfirst=True)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, column, ddl in (
        ("deficiency_chat_sessions", "close_reason", "VARCHAR(64)"),
        ("learning_paths", "weakness_signature", "TEXT"),
    ):
        if table not in tables:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if column not in columns:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            logger.info("%s.%s added", table, column)
