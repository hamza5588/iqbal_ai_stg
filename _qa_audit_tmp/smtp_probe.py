#!/usr/bin/env python3
"""SMTP auth probe. Never prints the password."""
import os
import smtplib
import ssl
import socket

user = (os.getenv("MAIL_USERNAME") or "").strip()
password = (os.getenv("MAIL_PASSWORD") or "").strip()
if len(password) >= 2 and password[0] == password[-1] and password[0] in ("'", '"'):
    password = password[1:-1].strip()
server = (os.getenv("MAIL_SERVER") or "mail.privateemail.com").strip()

print("mailbox", user)
print("pw_len", len(password))
print("pw_ends_at", password.endswith("@"))
print("server", server)


def try_login(host, port, mode, login_user):
    context = ssl.create_default_context()
    smtp = None
    try:
        if mode == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
        smtp.ehlo()
        if mode == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        auth = smtp.esmtp_features.get("auth", "")
        print(f"{host}:{port}/{mode} user={login_user} banner_auth={auth!r}")
        smtp.login(login_user, password)
        print(f"{host}:{port}/{mode} LOGIN_OK")
        return True
    except Exception as exc:
        print(f"{host}:{port}/{mode} FAIL {type(exc).__name__}: {exc}")
        return False
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass


print("dns", socket.getaddrinfo(server, 465)[0][4][0])
try_login(server, 465, "ssl", user)
try_login(server, 587, "starttls", user)
if "@" in user:
    local = user.split("@", 1)[0]
    print("retry_local_part")
    try_login(server, 465, "ssl", local)
    try_login(server, 587, "starttls", local)
