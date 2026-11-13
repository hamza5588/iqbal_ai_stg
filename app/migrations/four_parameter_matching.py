"""Four-parameter matching: cognitive level + printed difficulty storage.

Additive and idempotent:

* ``questions.cognitive_level`` / ``questions.printed_difficulty`` and the
  Guided Practice match columns are added only when missing (all nullable).
* ``lms_incorrect_answers`` is created with ``checkfirst``.
* Backfill only writes cells that are still NULL, from data the system already
  recorded: the Target-bank extraction metadata JSON first, then the legacy
  ``"Cognitive level: X"`` text in ``questions.explanation``. Existing values,
  answer keys, difficulty, taxonomy and approval status are never touched.

Rollback: ignore the nullable columns and drop ``lms_incorrect_answers``.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.models import lms_models  # noqa: F401 - registers the models on Base.metadata
from app.models.database_models import Base

logger = logging.getLogger(__name__)

NEW_COLUMNS = (
    ("questions", "cognitive_level", "VARCHAR(64)"),
    ("questions", "printed_difficulty", "VARCHAR(32)"),
    ("lms_guided_practice_attempts", "match_priority", "INTEGER"),
    ("lms_guided_practice_attempts", "target_question_id", "INTEGER"),
    ("lms_guided_practice_attempts", "match_json", "TEXT"),
)


def _add_columns(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, column, ddl in NEW_COLUMNS:
        if table not in tables:
            continue
        if column in {c["name"] for c in inspector.get_columns(table)}:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        logger.info("%s.%s added", table, column)


def backfill(engine) -> dict:
    """Fill NULL cognitive_level / printed_difficulty from recorded metadata."""
    from app.services.lms import question_attributes as qa

    stats = {"cognitive_from_bank": 0, "difficulty_from_bank": 0, "cognitive_from_explanation": 0}
    with engine.begin() as conn:
        bank_rows = conn.execute(
            text(
                "SELECT q.id, q.cognitive_level, q.printed_difficulty, i.extraction_metadata_json "
                "FROM questions q JOIN lms_target_bank_items i ON i.question_id = q.id "
                "WHERE q.cognitive_level IS NULL OR q.printed_difficulty IS NULL"
            )
        ).fetchall()
        for qid, cog, diff, meta in bank_rows:
            meta_cog, meta_diff = qa.from_bank_metadata(meta)
            if cog is None and meta_cog:
                conn.execute(text("UPDATE questions SET cognitive_level=:v WHERE id=:i AND cognitive_level IS NULL"),
                             {"v": meta_cog, "i": qid})
                stats["cognitive_from_bank"] += 1
            if diff is None and meta_diff:
                conn.execute(text("UPDATE questions SET printed_difficulty=:v WHERE id=:i AND printed_difficulty IS NULL"),
                             {"v": meta_diff[:32], "i": qid})
                stats["difficulty_from_bank"] += 1
        legacy = conn.execute(
            text(
                "SELECT id, explanation FROM questions "
                "WHERE cognitive_level IS NULL AND LOWER(explanation) LIKE '%cognitive level%'"
            )
        ).fetchall()
        for qid, explanation in legacy:
            cog = qa.cognitive_from_explanation(explanation)
            if cog:
                conn.execute(text("UPDATE questions SET cognitive_level=:v WHERE id=:i AND cognitive_level IS NULL"),
                             {"v": cog, "i": qid})
                stats["cognitive_from_explanation"] += 1
    return stats


def run_migration(engine) -> None:
    Base.metadata.tables["lms_incorrect_answers"].create(bind=engine, checkfirst=True)
    _add_columns(engine)
    stats = backfill(engine)
    if any(stats.values()):
        logger.info("four-parameter backfill: %s", stats)
