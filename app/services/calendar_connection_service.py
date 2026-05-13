"""Persist encrypted calendar credentials per user (Google OAuth refresh tokens, Apple CalDAV)."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.phase3_models import UserCalendarConnection
from app.utils.encryption import decrypt_api_key, encrypt_api_key

logger = logging.getLogger(__name__)

PROVIDER_GOOGLE_OAUTH = "google_oauth"
PROVIDER_APPLE_CALDAV = "apple_caldav"


def _mask_email(email: str) -> str:
    e = (email or "").strip()
    if "@" not in e:
        return e[:3] + "***" if e else ""
    local, dom = e.split("@", 1)
    if len(local) <= 2:
        masked = local[0] + "***"
    else:
        masked = local[0] + "***" + local[-1]
    return f"{masked}@{dom}"


def upsert_connection(
    db: Session,
    *,
    user_id: int,
    provider: str,
    payload_dict: Dict[str, Any],
    account_hint: Optional[str] = None,
) -> UserCalendarConnection:
    raw = json.dumps(payload_dict, default=str)
    enc = encrypt_api_key(raw)
    row = (
        db.query(UserCalendarConnection)
        .filter(
            UserCalendarConnection.user_id == int(user_id),
            UserCalendarConnection.provider == provider,
        )
        .first()
    )
    if row:
        row.encrypted_payload = enc
        row.account_hint = account_hint
    else:
        row = UserCalendarConnection(
            user_id=int(user_id),
            provider=provider,
            encrypted_payload=enc,
            account_hint=account_hint,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_google_oauth_tokens(
    db: Session,
    *,
    user_id: int,
    refresh_token: Optional[str],
    token_uri: str,
    scopes: List[str],
    email_hint: Optional[str] = None,
) -> UserCalendarConnection:
    if not refresh_token:
        raise ValueError("Google did not return a refresh token — remove the app from your Google Account connections and try again with consent.")
    payload = {
        "refresh_token": refresh_token,
        "token_uri": token_uri,
        "scopes": scopes,
    }
    return upsert_connection(
        db,
        user_id=user_id,
        provider=PROVIDER_GOOGLE_OAUTH,
        payload_dict=payload,
        account_hint=_mask_email(email_hint) if email_hint else None,
    )


def save_apple_caldav_credentials(
    db: Session,
    *,
    user_id: int,
    apple_id: str,
    app_specific_password: str,
) -> UserCalendarConnection:
    apple_id = (apple_id or "").strip()
    if not apple_id or not app_specific_password:
        raise ValueError("Apple ID and app-specific password are required")
    payload = {
        "apple_id": apple_id,
        "app_password": app_specific_password,
        "caldav_host": "https://caldav.icloud.com",
    }
    return upsert_connection(
        db,
        user_id=user_id,
        provider=PROVIDER_APPLE_CALDAV,
        payload_dict=payload,
        account_hint=_mask_email(apple_id),
    )


def delete_connection(db: Session, *, user_id: int, provider: str) -> bool:
    row = (
        db.query(UserCalendarConnection)
        .filter(
            UserCalendarConnection.user_id == int(user_id),
            UserCalendarConnection.provider == provider,
        )
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def list_connections_public(db: Session, *, user_id: int) -> List[Dict[str, Any]]:
    """Safe summary for UI / API (no secrets)."""
    rows = db.query(UserCalendarConnection).filter(UserCalendarConnection.user_id == int(user_id)).all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "provider": r.provider,
                "connected": True,
                "account_hint": r.account_hint,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )
    return out


def get_decrypted_google_payload(db: Session, *, user_id: int) -> Optional[Dict[str, Any]]:
    row = (
        db.query(UserCalendarConnection)
        .filter(
            UserCalendarConnection.user_id == int(user_id),
            UserCalendarConnection.provider == PROVIDER_GOOGLE_OAUTH,
        )
        .first()
    )
    if not row:
        return None
    try:
        raw = decrypt_api_key(row.encrypted_payload)
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Failed to decrypt google calendar payload: %s", exc)
        return None


def get_decrypted_apple_payload(db: Session, *, user_id: int) -> Optional[Dict[str, Any]]:
    row = (
        db.query(UserCalendarConnection)
        .filter(
            UserCalendarConnection.user_id == int(user_id),
            UserCalendarConnection.provider == PROVIDER_APPLE_CALDAV,
        )
        .first()
    )
    if not row:
        return None
    try:
        raw = decrypt_api_key(row.encrypted_payload)
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Failed to decrypt apple calendar payload: %s", exc)
        return None
