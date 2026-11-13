"""Idempotent SQLite/PostgreSQL equivalent of migration 012."""
from sqlalchemy import inspect, text


def migrate_question_metadata(engine):
    inspector = inspect(engine)
    if "questions" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("questions")}
    with engine.begin() as connection:
        for name, size in (("domain", 255), ("topic_label", 255), ("cognitive_level", 100)):
            if name not in columns:
                connection.execute(text(f"ALTER TABLE questions ADD COLUMN {name} VARCHAR({size})"))
