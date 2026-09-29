#!/usr/bin/env python3
"""Restart nginx (re-resolve flask_app1) and verify staging health."""
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import connect

APP = "/root/iqbal_ai_stg"


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 180) -> tuple[int, str, str]:
    print(">>>", cmd, flush=True)
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    stdout.channel.settimeout(timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if out:
        print(out[:8000], flush=True)
    if err:
        print("ERR:", err[:2000], flush=True)
    print(f"exit={code}", flush=True)
    return code, out, err


def main() -> int:
    ssh = connect(timeout=30)
    try:
        run(ssh, f"cd {APP} && docker compose restart nginx", timeout=180)
        run(ssh, "sleep 5")
        run(ssh, f"cd {APP} && docker compose ps", timeout=90)
        run(
            ssh,
            "curl -k -s -o /dev/null -w 'health:%{http_code}\\n' https://209.23.10.34/health; "
            "curl -k -s -o /dev/null -w 'lms:%{http_code}\\n' https://209.23.10.34/api/lms/health; "
            "curl -k -s -o /dev/null -w 'root:%{http_code}\\n' https://209.23.10.34/",
            timeout=60,
        )
        run(
            ssh,
            f"cd {APP} && docker compose exec -T flask_app1 "
            "curl -s -o /dev/null -w 'flask_local:%{http_code}\\n' http://127.0.0.1:5000/health",
            timeout=60,
        )
        run(
            ssh,
            f"cd {APP} && docker compose exec -T nginx "
            "wget -qO- --timeout=10 http://flask_app1:5000/health || echo nginx_to_flask_fail",
            timeout=60,
        )
        run(
            ssh,
            f"grep -n 'def recover_latex' {APP}/app/services/quiz/math_text.py; "
            f"grep -n 'def harvest_native_mcqs' {APP}/app/services/lms/mcq_utils.py",
            timeout=30,
        )
    finally:
        ssh.close()
    print("HEALTH_CHECK_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
