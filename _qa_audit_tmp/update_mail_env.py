#!/usr/bin/env python3
"""Update MAIL_* keys in /opt/flask-app/.env without printing secrets."""
from pathlib import Path
import os
import sys

ENV_PATH = Path("/opt/flask-app/.env")
USERNAME = os.environ.get("NEW_MAIL_USER", "").strip()
PASSWORD = os.environ.get("NEW_MAIL_PASS", "").strip()
SENDER = os.environ.get("NEW_MAIL_SENDER", USERNAME).strip()

if not USERNAME or not PASSWORD:
    print("missing NEW_MAIL_USER or NEW_MAIL_PASS", file=sys.stderr)
    sys.exit(1)

updates = {
    "MAIL_USERNAME": USERNAME,
    "MAIL_PASSWORD": PASSWORD,
    "MAIL_DEFAULT_SENDER": SENDER,
    "MAIL_SERVER": os.environ.get("NEW_MAIL_SERVER", "mail.privateemail.com").strip(),
    "MAIL_PORT": os.environ.get("NEW_MAIL_PORT", "465").strip(),
    "MAIL_USE_SSL": os.environ.get("NEW_MAIL_USE_SSL", "true").strip(),
    "MAIL_USE_TLS": os.environ.get("NEW_MAIL_USE_TLS", "false").strip(),
}

text = ENV_PATH.read_text(encoding="utf-8", errors="replace") if ENV_PATH.exists() else ""
lines = text.splitlines()
seen = set()
out = []
for line in lines:
    raw = line.strip()
    if not raw or raw.startswith("#") or "=" not in raw:
        out.append(line)
        continue
    key, _, _ = raw.partition("=")
    key = key.strip()
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)

for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")

ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
print("updated", ENV_PATH)
print("MAIL_USERNAME", USERNAME)
print("MAIL_DEFAULT_SENDER", SENDER)
print("MAIL_SERVER", updates["MAIL_SERVER"])
print("MAIL_PORT", updates["MAIL_PORT"])
