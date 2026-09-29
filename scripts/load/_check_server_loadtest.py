#!/usr/bin/env python3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import connect

REMOTE = "/root/iqbal_ai_stg"
ssh = connect(timeout=30)
for c in [
    "pkill -f run_lms_load_200.py 2>/dev/null; echo killed",
    f"cd {REMOTE} && docker compose exec -T flask_app1 python -c 'import requests; print(requests_ok)' 2>&1",
    f"""cd {REMOTE} && docker compose exec -d flask_app1 bash -c 'mkdir -p _qa_audit_tmp/load_test && python scripts/load/setup_loadtest_prereqs.py && nohup python scripts/load/run_lms_load_200.py > _qa_audit_tmp/load_test/run_stdout.log 2>&1 &'""",
    "sleep 3",
    f"cd {REMOTE} && docker compose exec -T flask_app1 tail -20 _qa_audit_tmp/load_test/run_stdout.log 2>&1",
    f"cd {REMOTE} && docker compose exec -T flask_app1 pgrep -af run_lms_load_200 || echo not_running",
]:
    print(">>>", c[:80])
    _, out, err = ssh.exec_command(c, timeout=120)
    print(out.read().decode()[:2000])
    e = err.read().decode()
    if e:
        print("E:", e[:500])
ssh.close()
