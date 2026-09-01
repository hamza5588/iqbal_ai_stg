#!/usr/bin/env python3
"""Inspect MAIL_PASSWORD shape only — never print the secret."""
from pathlib import Path

expected = "Iqbalai12@"
path = Path("/opt/flask-app/.env")
pw = None
user = None
for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    if line.startswith("MAIL_PASSWORD="):
        pw = line.split("=", 1)[1]
    elif line.startswith("MAIL_USERNAME="):
        user = line.split("=", 1)[1]

print("file_username", user)
print("file_pw_len", len(pw or ""))
print("file_pw_repr_ends", repr((pw or "")[:1]), repr((pw or "")[-1:]))
print("file_has_surrounding_quotes", bool(pw) and pw[:1] in "'\"" and pw[-1:] in "'\"")
print("file_matches_expected_len", len(pw or "") == len(expected))
print("file_equals_expected", pw == expected)
print("file_equals_expected_no_at", pw == expected[:-1])
print("file_contains_at", "@" in (pw or ""))
print("file_last_ord", ord(pw[-1]) if pw else None)

utils_env = Path("/opt/flask-app/app/utils/.env")
print("utils_env_exists", utils_env.exists())
