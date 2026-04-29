import logging
from sqlalchemy import create_engine, inspect, text

# Import your app config so we reuse the existing DATABASE_URL and engine options
from app.config import Config

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def ensure_rag_threads_columns(conn, inspector):
    """Add headings_* columns to rag_threads if they don't exist."""
    columns = {col["name"] for col in inspector.get_columns("rag_threads")}

    if "headings_ready" not in columns:
        logger.info("Adding column rag_threads.headings_ready ...")
        conn.execute(
            text(
                "ALTER TABLE rag_threads "
                "ADD COLUMN headings_ready BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )

    if "headings_count" not in columns:
        logger.info("Adding column rag_threads.headings_count ...")
        conn.execute(
            text(
                "ALTER TABLE rag_threads "
                "ADD COLUMN headings_count INTEGER NOT NULL DEFAULT 0"
            )
        )

    if "headings_last_scanned_at" not in columns:
        logger.info("Adding column rag_threads.headings_last_scanned_at ...")
        # Use TIMESTAMP WITHOUT TIME ZONE; adjust if you prefer timestamptz
        conn.execute(
            text(
                "ALTER TABLE rag_threads "
                "ADD COLUMN headings_last_scanned_at TIMESTAMP NULL"
            )
        )


def ensure_rag_headings_table(conn, inspector):
    """Create rag_headings table and indexes if they don't exist."""
    if inspector.has_table("rag_headings"):
        logger.info("Table rag_headings already exists, skipping creation.")
        return

    logger.info("Creating table rag_headings ...")
    conn.execute(
        text(
            """
            CREATE TABLE rag_headings (
                id SERIAL PRIMARY KEY,
                thread_id VARCHAR(255) NOT NULL
                    REFERENCES rag_threads(thread_id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
                page INTEGER NULL,
                heading VARCHAR(512) NOT NULL,
                normalized_heading VARCHAR(512) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
    )

    logger.info("Creating index idx_rag_headings_thread_user ...")
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_rag_headings_thread_user
            ON rag_headings (thread_id, user_id)
            """
        )
    )

    logger.info("Creating index idx_rag_headings_normalized ...")
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_rag_headings_normalized
            ON rag_headings (normalized_heading)
            """
        )
    )


def ensure_lessons_conversation_id_column(conn, inspector):
    """Add conversation_id column to lessons if it doesn't exist."""
    if not inspector.has_table("lessons"):
        logger.info("Table lessons does not exist, skipping conversation_id column.")
        return
    columns = {col["name"] for col in inspector.get_columns("lessons")}
    if "conversation_id" not in columns:
        logger.info("Adding column lessons.conversation_id ...")
        conn.execute(
            text("ALTER TABLE lessons ADD COLUMN conversation_id INTEGER NULL")
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_lessons_conversation_id "
                "ON lessons (conversation_id)"
            )
        )
    else:
        logger.info("Column lessons.conversation_id already exists, skipping.")


def ensure_conversation_summaries_table(conn, inspector):
    """Create conversation_summaries table and indexes if they don't exist."""
    if not inspector.has_table("conversation_summaries"):
        logger.info("Creating table conversation_summaries ...")
        conn.execute(
            text(
                """
                CREATE TABLE conversation_summaries (
                    id SERIAL PRIMARY KEY,
                    conversation_id INTEGER NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    lesson_id INTEGER NULL
                        REFERENCES lessons(id) ON DELETE SET NULL,
                    summary_text TEXT NOT NULL,
                    generated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_message_id INTEGER NULL,
                    last_message_timestamp TIMESTAMP NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
        )
    else:
        logger.info("Table conversation_summaries already exists, checking required columns.")
        columns = {col["name"] for col in inspector.get_columns("conversation_summaries")}
        if "last_message_id" not in columns:
            conn.execute(text("ALTER TABLE conversation_summaries ADD COLUMN last_message_id INTEGER NULL"))
        if "last_message_timestamp" not in columns:
            conn.execute(text("ALTER TABLE conversation_summaries ADD COLUMN last_message_timestamp TIMESTAMP NULL"))
        if "generated_at" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE conversation_summaries "
                    "ADD COLUMN generated_at TIMESTAMP NOT NULL DEFAULT NOW()"
                )
            )

    logger.info("Creating conversation summary indexes ...")
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_summaries_conversation_id
            ON conversation_summaries (conversation_id)
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_summaries_lesson_id
            ON conversation_summaries (lesson_id)
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_summaries_generated_at
            ON conversation_summaries (generated_at)
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_summaries_last_message_id
            ON conversation_summaries (last_message_id)
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_summaries_last_message_timestamp
            ON conversation_summaries (last_message_timestamp)
            """
        )
    )
    # Partial unique index allows multiple NULL coverage rows while deduping concrete coverage.
    conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_summary_last_message
            ON conversation_summaries (conversation_id, last_message_id)
            WHERE last_message_id IS NOT NULL
            """
        )
    )


def main():
    logger.info("Connecting to database: %s", Config.SQLALCHEMY_DATABASE_URI)
    engine = create_engine(
        Config.SQLALCHEMY_DATABASE_URI,
        **Config.SQLALCHEMY_ENGINE_OPTIONS,
    )

    inspector = inspect(engine)

    with engine.begin() as conn:
        ensure_rag_threads_columns(conn, inspector)
        # Recreate inspector if you want up-to-date metadata later,
        # but not required for our simple use here.
        ensure_rag_headings_table(conn, inspector)
        inspector = inspect(engine)
        ensure_conversation_summaries_table(conn, inspector)
        inspector = inspect(engine)
        ensure_lessons_conversation_id_column(conn, inspector)

    logger.info("Migration completed successfully.")


if __name__ == "__main__":
    main()