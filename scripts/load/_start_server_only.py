#!/usr/bin/env python3
"""Start load test ONLY on server (Docker flask_app1). Kill any host/local processes."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import connect

REMOTE = "/root/iqbal_ai_stg"

ssh = connect(timeout=30)

def run(cmd, timeout=600):
    print(">>", cmd[:120])
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    o = out.read().decode()
    e = err.read().decode()
    if o:
        print(o[:2000])
    if e:
        print("ERR:", e[:400])
    return o

run("pkill -f run_lms_load_200.py 2>/dev/null || true")
run(f"cd {REMOTE} && docker compose exec -T flask_app1 pkill -f run_lms_load_200.py 2>/dev/null || true")
time.sleep(2)

run(
    f"cd {REMOTE} && docker compose exec -T flask_app1 bash -lc "
    f"'mkdir -p _qa_audit_tmp/load_test && python scripts/load/setup_loadtest_prereqs.py'",
    timeout=600,
)

run(
    f"cd {REMOTE} && docker compose exec -d flask_app1 bash -lc "
    f"'nohup python scripts/load/run_lms_load_200.py > _qa_audit_tmp/load_test/run_stdout.log 2>&1 &'",
)

time.sleep(6)
run(f"cd {REMOTE} && docker compose exec -T flask_app1 pgrep -af run_lms_load_200.py || echo NOT_RUNNING")
run(f"cd {REMOTE} && docker compose exec -T flask_app1 tail -8 _qa_audit_tmp/load_test/run_stdout.log 2>/dev/null || true")
run("pgrep -af run_lms_load_200.py || echo HOST_CLEAR")

ssh.close()
print("\nDone: load test runs inside server Docker (flask_app1), not on your PC.")
