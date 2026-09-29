#!/usr/bin/env python3
"""Reset diagnostic/quiz state for loadtest_student_* accounts on staging only."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh_cli import APP, ssh_run

SQL = r"""
BEGIN;
CREATE TEMP TABLE lt_users AS
  SELECT id FROM users WHERE useremail LIKE 'loadtest_student_%@test.iqbalai.local';

UPDATE student_profiles
   SET diagnostic_completed = false,
       diagnostic_completed_at = NULL,
       diagnostic_assessment_id = NULL
 WHERE user_id IN (SELECT id FROM lt_users);

DELETE FROM attempt_answers
 WHERE attempt_id IN (
   SELECT id FROM assessment_attempts WHERE student_id IN (SELECT id FROM lt_users)
 );

UPDATE assignment_submissions
   SET attempt_id = NULL, status = 'not_started', submitted_at = NULL
 WHERE student_id IN (SELECT id FROM lt_users);

DELETE FROM assessment_attempts WHERE student_id IN (SELECT id FROM lt_users);
DELETE FROM deficiency_chat_sessions WHERE student_id IN (SELECT id FROM lt_users);
DELETE FROM student_topic_scores WHERE student_id IN (SELECT id FROM lt_users);

SELECT 'users' AS k, count(*) FROM lt_users
UNION ALL
SELECT 'profiles_incomplete', count(*) FROM student_profiles
 WHERE user_id IN (SELECT id FROM lt_users) AND diagnostic_completed = false;
COMMIT;
"""


def main() -> int:
    cmd = (
        f"cd {APP} && cat > /tmp/reset_loadtest.sql << 'EOSQL'\n{SQL}\nEOSQL\n"
        f"docker compose exec -T postgres sh -c 'psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -f -' < /tmp/reset_loadtest.sql"
    )
    print("Resetting load-test student diagnostic/quiz state...", flush=True)
    code, stdout, stderr = ssh_run(cmd, timeout=120)
    print(stdout, flush=True)
    if stderr.strip():
        print(stderr[:2000], flush=True)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
