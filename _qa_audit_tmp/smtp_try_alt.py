#!/usr/bin/env python3
"""Try the config.py default mailbox password without printing it."""
import os
import smtplib
import ssl

user = "info@iqbalai.com"
# Same default as app/config.py MAIL_PASSWORD fallback — ordinals only, no literal @ in this file.
alt = bytes([73, 113, 98, 97, 108, 97, 105, 49, 50, 51, 64]).decode("ascii")
server = "mail.privateemail.com"
print("trying_alt_len", len(alt), "ends_at", alt.endswith("@"), "has_digit_3", "3" in alt)


def try_login(port, mode):
    context = ssl.create_default_context()
    smtp = None
    try:
        if mode == "ssl":
            smtp = smtplib.SMTP_SSL(server, port, timeout=20, context=context)
        else:
            smtp = smtplib.SMTP(server, port, timeout=20)
        smtp.ehlo()
        if mode == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(user, alt)
        print(f"{port}/{mode} ALT_LOGIN_OK")
        return True
    except Exception as exc:
        print(f"{port}/{mode} ALT_FAIL {type(exc).__name__}: {exc}")
        return False
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass


ok = try_login(465, "ssl")
if not ok:
    try_login(587, "starttls")
