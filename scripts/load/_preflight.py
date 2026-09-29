#!/usr/bin/env python3
"""Health + Groq telemetry baseline before the 200-student load test."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from _ssh_cli import APP, ssh_run

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)

SQL = r"""
SELECT 'llm_events' AS k, COUNT(*)::text FROM llm_usage_events
UNION ALL
SELECT 'max_id', COALESCE(MAX(id),0)::text FROM llm_usage_events
UNION ALL
SELECT 'max_at', COALESCE(MAX(created_at)::text, 'none') FROM llm_usage_events;
SELECT provider, workflow, COUNT(*) AS n
  FROM llm_usage_events
 WHERE created_at > NOW() - INTERVAL '6 hours'
 GROUP BY 1, 2
 ORDER BY n DESC
 LIMIT 20;
"""


def main() -> int:
    print("=== health ===")
    for cmd in (
        "curl -k -sS --max-time 15 https://127.0.0.1/health",
        "curl -k -sS --max-time 15 https://127.0.0.1/api/lms/health",
    ):
        code, out, err = ssh_run(cmd, timeout=30)
        print(out.strip() or err.strip(), "exit", code)

    print("=== telemetry baseline ===")
    write = (
        f"cd {APP} && cat > /tmp/lt_baseline.sql << 'EOSQL'\n{SQL}\nEOSQL\n"
        f"docker compose exec -T postgres sh -c "
        f"'psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -f -' < /tmp/lt_baseline.sql"
    )
    code, out, err = ssh_run(write, timeout=60)
    print(out)
    if err.strip():
        print(err[:1500])
    payload = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "stdout": out,
        "stderr": err[:1500],
        "exit": code,
    }
    (OUT / "groq_baseline.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("wrote", OUT / "groq_baseline.json")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
