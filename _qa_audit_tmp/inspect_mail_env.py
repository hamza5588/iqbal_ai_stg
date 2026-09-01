from pathlib import Path

text = Path("/opt/flask-app/.env").read_text(errors="replace")
for line in text.splitlines():
    if not line.startswith("MAIL_"):
        continue
    key, _, value = line.partition("=")
    if key == "MAIL_PASSWORD":
        stripped = value.strip().strip("'").strip('"')
        markers = ("here", "changeme", "your_", "username", "password_here")
        print(
            key,
            "len=" + str(len(stripped)),
            "placeholder=" + str(any(m in stripped.lower() for m in markers)),
            "quoted=" + str(bool(value) and value[:1] in "'\"" and value[-1:] in "'\""),
        )
    else:
        print(f"{key}={value}")
