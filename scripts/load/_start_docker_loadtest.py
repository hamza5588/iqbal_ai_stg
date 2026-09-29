#!/usr/bin/env python3
"""Start load test only inside staging Docker flask_app1 (not from local Windows)."""
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import connect

REMOTE = "/root/iqbal_ai_stg"

ssh = connect(timeout=30)


def run(cmd, timeout=120):
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    return out.read().decode(), err.read().decode()


print("kill host processes")
o, e = run("pkill -f run_lms_load_200.py; pkill -f setup_loadtest_prereqs.py; echo host_kill_done")
print(o, e)

print("kill inside container")
o, e = run(
    f"cd {REMOTE} && docker compose exec -T flask_app1 pkill -f run_lms_load_200.py; echo docker_kill_done"
)
print(o, e)

print("start inside flask_app1")
start = (
    f"cd {REMOTE} && docker compose exec -d flask_app1 "
    "sh -c 'nohup python scripts/load/run_lms_load_200.py "
    "> _qa_audit_tmp/load_test/run_stdout.log 2>&1 < /dev/null &'"
)
o, e = run(start, timeout=30)
print(o, e)

time.sleep(6)
o, e = run(
    f"cd {REMOTE} && docker compose exec -T flask_app1 pgrep -af run_lms_load_200.py"
)
print("DOCKER PROC:\n", o, e)

o, e = run("pgrep -af run_lms_load_200.py || echo HOST_CLEAR")
print("HOST PROC:\n", o)

o, e = run(
    f"cd {REMOTE} && docker compose exec -T flask_app1 "
    "tail -8 _qa_audit_tmp/load_test/run_stdout.log"
)
print("LOG:\n", o)
ssh.close()
