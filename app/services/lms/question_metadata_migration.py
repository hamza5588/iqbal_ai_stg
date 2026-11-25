"""Idempotent SQLite/PostgreSQL equivalent of migration 012."""
from sqlalchemy import inspect, text

# Must match the CheckConstraint "check_creation_mode" on Assessment in lms_models.
ASSESSMENT_CREATION_MODES = ("manual", "pdf_qa_auto", "pdf_ai", "mixed", "pdf_content_generated")


def migrate_question_metadata(engine):
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if "questions" in tables:
        columns = {column["name"] for column in inspector.get_columns("questions")}
        with engine.begin() as connection:
            for name, size in (
                ("domain", 255),
                ("topic_label", 255),
                ("cognitive_level", 100),
                ("printed_difficulty", 32),
            ):
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE questions ADD COLUMN {name} VARCHAR({size})"))

    # Older PostgreSQL databases keep the creation_mode check from before
    # 'pdf_content_generated' existed, so quizzes generated from learning-content
    # PDFs fail to save. SQLite cannot alter constraints; it is only used for
    # fresh local databases, which get the current constraint from create_all.
    if "assessments" in tables and engine.dialect.name == "postgresql":
        allowed = ",".join(f"'{mode}'" for mode in ASSESSMENT_CREATION_MODES)
        with engine.begin() as connection:
            current = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'check_creation_mode' "
                    "AND conrelid = 'assessments'::regclass"
                )
            ).scalar()
            if current is None or "pdf_content_generated" not in current:
                connection.execute(text("ALTER TABLE assessments DROP CONSTRAINT IF EXISTS check_creation_mode"))
                connection.execute(
                    text(
                        "ALTER TABLE assessments ADD CONSTRAINT check_creation_mode "
                        f"CHECK (creation_mode IN ({allowed}))"
                    )
                )
