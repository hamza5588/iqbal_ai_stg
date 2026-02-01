"""
Fix boolean columns in PostgreSQL.
The migration created INTEGER columns but SQLAlchemy expects BOOLEAN.
"""
from sqlalchemy import create_engine, text
from app.config import Config

engine = create_engine(Config.DATABASE_URL)
with engine.connect() as conn:
    # Fix has_document: drop default -> change type -> set new default
    conn.execute(text("ALTER TABLE rag_threads ALTER COLUMN has_document DROP DEFAULT"))
    conn.execute(text("ALTER TABLE rag_threads ALTER COLUMN has_document TYPE BOOLEAN USING has_document::int::boolean"))
    conn.execute(text("ALTER TABLE rag_threads ALTER COLUMN has_document SET DEFAULT false"))
    
    # Fix lesson_finalized: drop default -> change type -> set new default
    conn.execute(text("ALTER TABLE rag_threads ALTER COLUMN lesson_finalized DROP DEFAULT"))
    conn.execute(text("ALTER TABLE rag_threads ALTER COLUMN lesson_finalized TYPE BOOLEAN USING lesson_finalized::int::boolean"))
    conn.execute(text("ALTER TABLE rag_threads ALTER COLUMN lesson_finalized SET DEFAULT false"))
    
    conn.commit()
    print("Fixed boolean columns successfully!")