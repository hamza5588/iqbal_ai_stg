"""Password hashing helpers.

Accounts created before hashing was introduced still hold a plaintext value in
``users.password``. ``verify_password`` accepts those once and tells the caller to
re-store the value hashed, so existing users are upgraded at their next login with
no forced reset.
"""
from __future__ import annotations

import hmac
from typing import Tuple

from werkzeug.security import check_password_hash, generate_password_hash

_HASH_PREFIXES = ("pbkdf2:", "scrypt:")
_METHOD = "pbkdf2:sha256"  # ~103 chars, fits users.password VARCHAR(255)
MIN_PASSWORD_LENGTH = 8


def is_hashed(stored: str) -> bool:
    return bool(stored) and stored.startswith(_HASH_PREFIXES)


def hash_password(password: str) -> str:
    return generate_password_hash(password, method=_METHOD)


def verify_password(stored: str, candidate: str) -> Tuple[bool, bool]:
    """Return (matches, needs_rehash)."""
    if not stored or not candidate:
        return False, False
    if is_hashed(stored):
        return check_password_hash(stored, candidate), False
    # legacy plaintext row
    ok = hmac.compare_digest(stored.encode("utf-8"), candidate.encode("utf-8"))
    return ok, ok


def password_problem(password: str) -> str:
    """Empty string when acceptable, otherwise a user-facing reason."""
    if not password or not password.strip():
        return "Password is required"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    return ""
