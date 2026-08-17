"""SMTP sending for auth emails.

Flask-Mail's SMTP_SSL path has no timeout and no TLS fallback, so Namecheap
(privateemail) on port 465 often surfaces as a generic "Failed to send
verification email". This helper uses a timeout, an SSL context (no removed
keyfile kwargs), and falls back to 587 STARTTLS when 465 fails.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import List, Optional, Sequence, Tuple

from flask import current_app

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 20


class MailConfigError(RuntimeError):
    """Mail is missing a required setting (sender/username/password/server)."""


def _cfg(key: str, default=None):
    try:
        return current_app.config.get(key, default)
    except RuntimeError:
        return default


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _sender_address() -> str:
    raw = _cfg("MAIL_DEFAULT_SENDER") or _cfg("MAIL_USERNAME") or ""
    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        return str(raw[1]).strip()
    _, addr = parseaddr(str(raw))
    return (addr or str(raw)).strip()


def _from_header() -> str:
    raw = _cfg("MAIL_DEFAULT_SENDER") or _cfg("MAIL_USERNAME") or ""
    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        name, addr = str(raw[0]).strip(), str(raw[1]).strip()
        return formataddr((name, addr)) if addr else ""
    name, addr = parseaddr(str(raw))
    email = (addr or str(raw)).strip()
    if not email:
        return ""
    return formataddr((name or "Iqbal AI", email))


def _recipients(to: Sequence[str] | str) -> List[str]:
    if isinstance(to, str):
        items = [to]
    else:
        items = list(to)
    cleaned = [str(r).strip() for r in items if str(r).strip()]
    if not cleaned:
        raise MailConfigError("No email recipients were provided.")
    return cleaned


def _build_message(subject: str, recipients: Sequence[str], body: str) -> EmailMessage:
    from_header = _from_header()
    if not from_header:
        raise MailConfigError("MAIL_DEFAULT_SENDER / MAIL_USERNAME is not configured.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = ", ".join(recipients)
    msg.set_content(body or "")
    return msg


def _attempts() -> List[Tuple[str, int, bool, bool]]:
    """Primary config first, then Namecheap's alternate 587 STARTTLS."""
    server = str(_cfg("MAIL_SERVER") or "mail.privateemail.com").strip()
    port = int(_cfg("MAIL_PORT") or 465)
    use_ssl = _truthy(_cfg("MAIL_USE_SSL", True))
    use_tls = _truthy(_cfg("MAIL_USE_TLS", False))
    attempts = [(server, port, use_ssl, use_tls)]
    if not (port == 587 and use_tls and not use_ssl):
        attempts.append((server, 587, False, True))
    # De-dupe while preserving order
    seen = set()
    unique = []
    for item in attempts:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _connect_and_send(
    *,
    server: str,
    port: int,
    use_ssl: bool,
    use_tls: bool,
    username: str,
    password: str,
    envelope_from: str,
    recipients: Sequence[str],
    payload: bytes,
    timeout: int,
) -> None:
    context = ssl.create_default_context()
    smtp: Optional[smtplib.SMTP] = None
    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(server, port, timeout=timeout, context=context)
        else:
            smtp = smtplib.SMTP(server, port, timeout=timeout)
        smtp.ehlo()
        if use_tls and not use_ssl:
            smtp.starttls(context=context)
            smtp.ehlo()
        if username and password:
            smtp.login(username, password)
        smtp.sendmail(envelope_from, list(recipients), payload)
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass


def send_email(subject: str, recipients: Sequence[str] | str, body: str) -> None:
    """Send a plaintext email. Raises MailConfigError or smtplib errors."""
    if _truthy(_cfg("MAIL_SUPPRESS_SEND")) or _truthy(_cfg("TESTING")):
        logger.info("Mail send suppressed (TESTING/MAIL_SUPPRESS_SEND): %s", subject)
        return

    to_list = _recipients(recipients)
    username = str(_cfg("MAIL_USERNAME") or "").strip()
    password = str(_cfg("MAIL_PASSWORD") or "").strip()
    if not username or not password:
        raise MailConfigError("MAIL_USERNAME / MAIL_PASSWORD is not configured.")

    envelope_from = _sender_address()
    if not envelope_from:
        raise MailConfigError("MAIL_DEFAULT_SENDER / MAIL_USERNAME is not configured.")

    message = _build_message(subject, to_list, body)
    payload = message.as_bytes()
    timeout = int(_cfg("MAIL_TIMEOUT") or _DEFAULT_TIMEOUT)
    errors: List[str] = []

    for server, port, use_ssl, use_tls in _attempts():
        mode = "SSL" if use_ssl else ("STARTTLS" if use_tls else "plain")
        try:
            _connect_and_send(
                server=server,
                port=port,
                use_ssl=use_ssl,
                use_tls=use_tls,
                username=username,
                password=password,
                envelope_from=envelope_from,
                recipients=to_list,
                payload=payload,
                timeout=timeout,
            )
            logger.info("Email sent via %s:%s (%s) to %s", server, port, mode, to_list)
            return
        except Exception as exc:
            logger.warning(
                "SMTP %s:%s (%s) failed: %s",
                server,
                port,
                mode,
                exc,
            )
            errors.append(f"{server}:{port}/{mode}: {exc}")

    raise smtplib.SMTPException("All SMTP attempts failed: " + " | ".join(errors))
