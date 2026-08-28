#!/usr/bin/env python3
"""Run LMS SQL migrations (optional; tables also created via init_db/create_all)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "docs" / "development-plan" / "migrations"


def main():
    from app import create_app
    from app.utils.db import get_engine
    from sqlalchemy import text

    app = create_app()
    with app.app_context():
        engine = get_engine()
        if not MIGRATIONS.exists():
            print("No migrations folder found; relying on SQLAlchemy create_all")
            return
        sql_files = sorted(MIGRATIONS.glob("*.sql"))
        if not sql_files:
            print("No .sql migration files; tables created via init_db")
            return
        with engine.begin() as conn:
            for path in sql_files:
                sql = path.read_text(encoding="utf-8")
                for stmt in sql.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(text(stmt))
                print(f"Applied {path.name}")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
