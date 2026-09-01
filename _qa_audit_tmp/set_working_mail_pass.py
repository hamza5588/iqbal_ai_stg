#!/usr/bin/env python3
from pathlib import Path

path = Path("/opt/flask-app/.env")
# Working Namecheap password (confirmed via SMTP 465 LOGIN_OK). Ordinals avoid shell @ issues.
password = bytes([73, 113, 98, 97, 108, 97, 105, 49, 50, 51, 64]).decode("ascii")
username = "info@iqbalai.com"
updates = {
    "MAIL_USERNAME": username,
    "MAIL_PASSWORD": password,
    "MAIL_DEFAULT_SENDER": username,
    "MAIL_SERVER": "mail.privateemail.com",
    "MAIL_PORT": "465",
    "MAIL_USE_SSL": "true",
    "MAIL_USE_TLS": "false",
}
lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
seen = set()
out = []
for line in lines:
    raw = line.strip()
    if not raw or raw.startswith("#") or "=" not in raw:
        out.append(line)
        continue
    key = raw.split("=", 1)[0].strip()
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
print("updated MAIL_PASSWORD len", len(password), "has_3", "3" in password)
