"""OpenSSH CLI helper for staging. Paramiko EOFError on this host — use native ssh."""
from __future__ import annotations

import subprocess
from pathlib import Path

HOST = "209.23.10.34"
USER = "root"
KEY_PATH = Path.home() / ".ssh" / "iqbalai_server_209_34"
APP = "/root/iqbal_ai_stg"


def ssh_run(cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    if not KEY_PATH.is_file():
        raise FileNotFoundError(f"SSH key not found: {KEY_PATH}")
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=20",
        "-o",
        "ServerAliveCountMax=6",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-i",
        str(KEY_PATH),
        f"{USER}@{HOST}",
        cmd,
    ]
    r = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return r.returncode, r.stdout, r.stderr
