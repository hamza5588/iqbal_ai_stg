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

    logger.info("Migration completed successfully.")


if __name__ == "__main__":
    main()