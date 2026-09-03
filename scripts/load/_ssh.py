"""SSH to staging with the local iqbalai ed25519 key — never a password."""
from __future__ import annotations

from pathlib import Path

import paramiko

HOST = "209.23.10.34"
USER = "root"
KEY_PATH = Path.home() / ".ssh" / "iqbalai_server_209_34"


def connect(timeout: int = 30) -> paramiko.SSHClient:
    if not KEY_PATH.is_file():
        raise FileNotFoundError(f"SSH key not found: {KEY_PATH}")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        HOST,
        username=USER,
        key_filename=str(KEY_PATH),
        look_for_keys=False,
        allow_agent=False,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
    )
    return ssh
