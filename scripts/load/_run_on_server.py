#!/usr/bin/env python3
"""Upload load test scripts and run on staging server (not local)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import connect

REMOTE = "/root/iqbal_ai_stg"
LOCAL = Path(__file__).resolve().parents[2]

FILES = [
    "scripts/load/setup_loadtest_prereqs.py",
    "scripts/load/run_lms_load_200.py",
]

ssh = connect(timeout=30)
sftp = ssh.open_sftp()
try:
    sftp.mkdir(f"{REMOTE}/scripts/load")
except OSError:
    pass
for rel in FILES:
    src = LOCAL / rel
    dst = f"{REMOTE}/{rel.replace(chr(92), '/')}"
    print("upload", rel)
    sftp.put(str(src), dst)
sftp.close()

# Stop any prior load test on server
stop_cmd = "pkill -f 'run_lms_load_200.py' 2>/dev/null; sleep 1; echo stopped_old"
_, out, _ = ssh.exec_command(stop_cmd, timeout=30)
print(out.read().decode())

# Ensure requests + run setup then load test in background on SERVER
run_cmd = f"""cd {REMOTE} && mkdir -p _qa_audit_tmp/load_test && \
(pip3 install -q requests urllib3 2>/dev/null || pip install -q requests urllib3) && \
python3 scripts/load/setup_loadtest_prereqs.py && \
nohup python3 scripts/load/run_lms_load_200.py > _qa_audit_tmp/load_test/run_stdout.log 2>&1 & \
echo SERVER_PID=$! && sleep 2 && tail -5 _qa_audit_tmp/load_test/run_stdout.log 2>/dev/null || true"""
_, out, err = ssh.exec_command(run_cmd, timeout=600)
print(out.read().decode())
e = err.read().decode()
if e:
    print("stderr:", e[:800])
ssh.close()
print("Load test started ON SERVER (not local).")
