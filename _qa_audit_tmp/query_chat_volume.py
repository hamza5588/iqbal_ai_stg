from __future__ import annotations
import json, os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])
tables_sql = "SELECT tablename FROM pg_tables WHERE schemaname='public'"
with engine.connect() as c:
    tables = [r[0] for r in c.execute(text(tables_sql))]
    out = {"tables_chat": [t for t in tables if "chat" in t or "lesson" in t or "message" in t]}
    for t, q in [
        ("lesson_chat_history", "SELECT COUNT(*) FROM lesson_chat_history"),
        ("chat_history", "SELECT COUNT(*) FROM chat_history"),
        ("conversations", "SELECT COUNT(*) FROM conversations"),
        ("lessons", "SELECT COUNT(*) FROM lessons"),
        ("messages", "SELECT COUNT(*) FROM messages"),
    ]:
        if t in tables:
            try:
                out[t] = c.execute(text(q)).scalar()
            except Exception as e:
                out[t] = str(e)
    # user-role messages if columns exist
    if "chat_history" in tables:
        cols = [r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='chat_history'"
        ))]
        out["chat_history_cols"] = cols
print(json.dumps(out, default=str, indent=2))
