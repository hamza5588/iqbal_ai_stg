#!/usr/bin/env python3
"""Groq RPM + per-student usage after the load test (events with id > baseline)."""
from __future__ import annotations

import json
from pathlib import Path

from _ssh_cli import APP, ssh_run

OUT = Path(__file__).resolve().parent / "results"
BASELINE_ID = 3781

SQL = f"""
SELECT COUNT(*) AS new_events,
       SUM(CASE WHEN success THEN 1 ELSE 0 END) AS ok,
       SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) AS fail
  FROM llm_usage_events WHERE id > {BASELINE_ID};

SELECT provider, workflow, success, error_class,
       COUNT(*) AS n
  FROM llm_usage_events WHERE id > {BASELINE_ID}
 GROUP BY 1,2,3,4 ORDER BY n DESC;

SELECT date_trunc('minute', created_at) AS minute,
       COUNT(*) AS groq_calls,
       SUM(CASE WHEN success THEN 1 ELSE 0 END) AS ok,
       SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) AS fail,
       COUNT(DISTINCT user_id) AS students
  FROM llm_usage_events
 WHERE id > {BASELINE_ID} AND lower(provider) LIKE '%groq%'
 GROUP BY 1 ORDER BY 1;

SELECT COUNT(*) AS students_with_groq,
       ROUND(AVG(n), 2) AS avg_groq_per_student,
       MIN(n) AS min_groq,
       MAX(n) AS max_groq,
       PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY n) AS p50,
       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY n) AS p95
  FROM (
    SELECT user_id, COUNT(*) AS n
      FROM llm_usage_events
     WHERE id > {BASELINE_ID} AND lower(provider) LIKE '%groq%' AND user_id IS NOT NULL
     GROUP BY user_id
  ) s;

SELECT error_class, LEFT(error_message, 180) AS err, COUNT(*) AS n
  FROM llm_usage_events
 WHERE id > {BASELINE_ID} AND success = false
 GROUP BY 1,2 ORDER BY n DESC LIMIT 20;
"""


def main() -> int:
    cmd = (
        f"cd {APP} && cat > /tmp/lt_groq_after.sql << 'EOSQL'\n{SQL}\nEOSQL\n"
        f"docker compose exec -T postgres sh -c "
        f"'psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -f -' < /tmp/lt_groq_after.sql"
    )
    code, out, err = ssh_run(cmd, timeout=90)
    print(out)
    if err.strip():
        print(err[:2000])
    (OUT / "groq_after.txt").write_text(out + "\n" + err, encoding="utf-8")
    print("wrote", OUT / "groq_after.txt")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
