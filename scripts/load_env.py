"""Load project env for CLI scripts (seed, backfill, migrations)."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse


def load_project_env() -> None:
    """Load local .env only — never server_files/.env (staging)."""
    root = Path(__file__).resolve().parents[1]
    candidates = [
        root / ".env",
        root / "app" / "utils" / ".env",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip().lstrip("\ufeff")
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ[key] = val

    if not os.environ.get("DATABASE_URL") and os.environ.get("POSTGRES_USER"):
        user = os.environ["POSTGRES_USER"]
        password = os.environ.get("POSTGRES_PASSWORD", "")
        db = os.environ.get("POSTGRES_DB", "postgres")
        host = os.environ.get("POSTGRES_HOST", "localhost")
        port = os.environ.get("POSTGRES_PORT", "5432")
        os.environ["DATABASE_URL"] = f"postgresql://{user}:{password}@{host}:{port}/{db}"


def describe_database_target() -> str:
    """Safe one-line DB target for CLI logs (no password)."""
    url = os.environ.get("DATABASE_URL", "postgresql://myuser@localhost:5432/mydatabase (default)")
    if url.startswith("sqlite"):
        return url
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    db = (parsed.path or "/mydatabase").lstrip("/")
    user = parsed.username or "?"
    return f"{user}@{host}:{port}/{db}"
