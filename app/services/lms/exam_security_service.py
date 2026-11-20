"""In-app exam security helpers (IqbalAI only — no third-party SEB client).

Modes
-----
- ``standard``: soft browser controls + server logging/heartbeat/events.
- ``supervised``: same soft lock + optional exam window / stricter teacher knobs.
- Legacy ``seb`` values in the DB are treated as ``standard`` (SEB removed).

Residual risks: soft lock is not OS lockdown; second devices / phone photos remain.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urljoin

from flask import has_request_context, request, session

from app.config import Config
from app.models.lms_models import Assessment, AssessmentAttempt, ExamSecurityEvent
from app.services.lms.exceptions import LMSValidationError
from app.utils.db import get_db

logger = logging.getLogger(__name__)

SECURITY_MODES = ("standard", "supervised")
# Legacy DB values accepted then normalized away from third-party SEB.
_LEGACY_SEB_MODES = frozenset({"seb"})
EVENT_TYPES = frozenset(
    {
        "fullscreen_exit",
        "tab_hidden",
        "window_blur",
        "reload",
        "session_conflict",
        "seb_verify_failed",
        "seb_verify_ok",
        "unauthorized_client",
        "heartbeat_miss",
        "resume",
        "submit",
        "timeout_finalize",
        "invigilator_override",
        "api_forbidden",
        "focus_loss",
        "copy_blocked",
        "paste_blocked",
        "context_menu_blocked",
        "other",
    }
)

# Soft-lock events are suspected signals only — never auto-fail solely on these.
SOFT_SIGNAL_EVENTS = frozenset(
    {
        "fullscreen_exit",
        "tab_hidden",
        "window_blur",
        "focus_loss",
        "reload",
        "copy_blocked",
        "paste_blocked",
        "context_menu_blocked",
        "heartbeat_miss",
    }
)


def _seb_bypass_allowed() -> bool:
    """Test-only bypass. Never enable in production."""
    if os.getenv("EXAM_SECURITY_SEB_BYPASS", "").strip() not in ("1", "true", "yes"):
        return False
    env = (os.getenv("FLASK_ENV") or os.getenv("ENV") or "").lower()
    return env in ("development", "testing", "test") or bool(os.getenv("PYTEST_CURRENT_TEST"))


def policy_for(assessment: Assessment) -> dict[str, Any]:
    mode = (getattr(assessment, "security_mode", None) or "standard").strip().lower()
    if mode in _LEGACY_SEB_MODES:
        mode = "standard"
    if mode not in SECURITY_MODES:
        mode = "standard"
    # CEO policy: no third-party Safe Exam Browser — in-app soft lock only.
    require_seb = False
    extra = {}
    raw = getattr(assessment, "security_policy_json", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                extra = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return {
        "security_mode": mode,
        "require_seb": require_seb,
        "allow_resume": bool(getattr(assessment, "allow_resume", True)),
        "max_interruptions": getattr(assessment, "max_interruptions", None),
        "max_concurrent_sessions": int(getattr(assessment, "max_concurrent_sessions", 1) or 1),
        "exam_window_start": getattr(assessment, "exam_window_start", None),
        "exam_window_end": getattr(assessment, "exam_window_end", None),
        "auto_submit_on_expiry": bool(getattr(assessment, "auto_submit_on_expiry", True)),
        "warn_on_focus_loss": bool(getattr(assessment, "warn_on_focus_loss", True)),
        "seb_config_key_hash": getattr(assessment, "seb_config_key_hash", None),
        "extra": extra,
    }


def serialize_policy(assessment: Assessment) -> dict[str, Any]:
    p = policy_for(assessment)
    return {
        "security_mode": p["security_mode"],
        "require_seb": p["require_seb"],
        "allow_resume": p["allow_resume"],
        "max_interruptions": p["max_interruptions"],
        "max_concurrent_sessions": p["max_concurrent_sessions"],
        "exam_window_start": p["exam_window_start"].isoformat() if p["exam_window_start"] else None,
        "exam_window_end": p["exam_window_end"].isoformat() if p["exam_window_end"] else None,
        "auto_submit_on_expiry": p["auto_submit_on_expiry"],
        "warn_on_focus_loss": p["warn_on_focus_loss"],
        "seb_configured": False,
        "third_party_seb": False,
    }


def _parse_browser_exam_keys(assessment: Assessment) -> list[str]:
    raw = getattr(assessment, "seb_browser_exam_keys_json", None)
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(x).strip().lower() for x in data if str(x).strip()]


def ensure_seb_config_key(assessment: Assessment) -> str:
    """Create a durable Config Key hash for this assessment if missing."""
    existing = getattr(assessment, "seb_config_key_hash", None)
    if existing:
        return existing
    material = "|".join(
        [
            "iqbalai-seb-v1",
            str(assessment.id),
            str(Config.SECRET_KEY or "dev"),
            secrets.token_hex(16),
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    assessment.seb_config_key_hash = digest
    db = get_db()
    db.commit()
    return digest


def expected_request_hash(absolute_url: str, key_hex: str) -> str:
    """SEB: SHA256(absoluteURL + key) as lowercase hex."""
    payload = f"{absolute_url}{key_hex}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def absolute_request_url() -> str:
    if not has_request_context():
        return ""
    # Prefer reconstructed URL without fragment; SEB hashes the absolute request URL.
    return request.url.split("#", 1)[0]


def verify_seb_headers(assessment: Assessment, *, absolute_url: Optional[str] = None) -> dict[str, Any]:
    """Validate SEB Config Key and/or Browser Exam Key headers.

    Returns ``{ok, method, detail}``. Client headers are untrusted inputs;
    matching a stored key is necessary but not sufficient against a forged client
    that knows the key. Prefer institutional device control for Mode C.
    """
    if _seb_bypass_allowed():
        return {"ok": True, "method": "test_bypass", "detail": "EXAM_SECURITY_SEB_BYPASS"}

    url = absolute_url or absolute_request_url()
    if not url:
        return {"ok": False, "method": None, "detail": "missing_request_url"}

    config_hdr = None
    bek_hdr = None
    if has_request_context():
        config_hdr = (request.headers.get("X-SafeExamBrowser-ConfigKeyHash") or "").strip().lower()
        bek_hdr = (request.headers.get("X-SafeExamBrowser-RequestHash") or "").strip().lower()

    config_key = getattr(assessment, "seb_config_key_hash", None) or ""
    if config_key and config_hdr:
        expected = expected_request_hash(url, config_key.lower())
        if hmac.compare_digest(expected, config_hdr):
            return {"ok": True, "method": "config_key", "detail": "matched"}

    for bek in _parse_browser_exam_keys(assessment):
        if not bek_hdr:
            break
        expected = expected_request_hash(url, bek)
        if hmac.compare_digest(expected, bek_hdr):
            return {"ok": True, "method": "browser_exam_key", "detail": "matched"}

    if not config_hdr and not bek_hdr:
        return {"ok": False, "method": None, "detail": "missing_seb_headers"}
    return {"ok": False, "method": None, "detail": "seb_key_mismatch"}


def assert_exam_window(assessment: Assessment, now: Optional[datetime] = None) -> None:
    now = now or datetime.utcnow()
    start = getattr(assessment, "exam_window_start", None)
    end = getattr(assessment, "exam_window_end", None)
    if start and now < start:
        raise LMSValidationError("This examination is not open yet.")
    if end and now > end:
        raise LMSValidationError("This examination window has ended.")


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def bind_attempt_security(
    attempt: AssessmentAttempt,
    assessment: Assessment,
    *,
    seb_ok: bool,
    client_fingerprint: Optional[str] = None,
) -> None:
    policy = policy_for(assessment)
    attempt.security_mode = policy["security_mode"]
    attempt.seb_verified = bool(seb_ok) if policy["require_seb"] else False
    if not attempt.session_token:
        attempt.session_token = new_session_token()
    if client_fingerprint:
        attempt.client_fingerprint = str(client_fingerprint)[:128]
    get_db().commit()


def require_active_session(
    attempt: AssessmentAttempt,
    assessment: Assessment,
    *,
    session_token: Optional[str] = None,
) -> None:
    """Reject cross-session / finalized writes."""
    if attempt.status != "in_progress":
        raise LMSValidationError("This examination attempt is no longer active.")
    if session_token and attempt.session_token and not hmac.compare_digest(
        str(attempt.session_token), str(session_token)
    ):
        record_event(
            attempt,
            "session_conflict",
            severity="high",
            message="Mismatched examination session token",
            verified=True,
        )
        raise LMSValidationError("Examination session conflict. Re-open the exam from your class page.")


def authorize_start(
    assessment: Assessment,
    *,
    client_fingerprint: Optional[str] = None,
) -> dict[str, Any]:
    """Pre-flight checks before creating/resuming an attempt."""
    if assessment.assessment_type not in ("quiz", "diagnostic"):
        return {"ok": True, "policy": policy_for(assessment), "seb": {"ok": True, "method": None}}

    policy = policy_for(assessment)
    assert_exam_window(assessment)
    # Third-party SEB is disabled; always allow in-app soft lock clients.
    seb = {"ok": True, "method": None, "detail": "seb_disabled_in_app_only"}
    return {"ok": True, "policy": policy, "seb": seb, "client_fingerprint": client_fingerprint}


def record_event(
    attempt: AssessmentAttempt,
    event_type: str,
    *,
    severity: str = "info",
    message: Optional[str] = None,
    context: Optional[dict] = None,
    verified: bool = False,
) -> ExamSecurityEvent:
    et = event_type if event_type in EVENT_TYPES else "other"
    sev = severity if severity in ("info", "warn", "high", "critical") else "info"
    # Soft browser signals are never "verified cheating".
    if et in SOFT_SIGNAL_EVENTS:
        verified = False
        if sev == "critical":
            sev = "warn"
    ctx = dict(context or {})
    if has_request_context():
        ctx.setdefault("path", request.path)
        # Avoid logging full User-Agent dumps with PII beyond what's needed.
        ua = (request.headers.get("User-Agent") or "")[:180]
        if ua:
            ctx.setdefault("ua", ua)
    row = ExamSecurityEvent(
        attempt_id=attempt.id,
        student_id=attempt.student_id,
        assessment_id=attempt.assessment_id,
        event_type=et,
        severity=sev,
        verified=bool(verified),
        message=(message or "")[:512] or None,
        context_json=json.dumps(ctx, default=str)[:4000] if ctx else None,
    )
    db = get_db()
    db.add(row)
    attempt.last_security_event_at = datetime.utcnow()
    if et in ("tab_hidden", "window_blur", "focus_loss", "fullscreen_exit"):
        attempt.focus_loss_count = int(attempt.focus_loss_count or 0) + 1
        attempt.interruption_count = int(attempt.interruption_count or 0) + 1
    db.commit()
    return row


def list_events_for_attempt(attempt_id: int, limit: int = 200) -> list[dict]:
    db = get_db()
    rows = (
        db.query(ExamSecurityEvent)
        .filter(ExamSecurityEvent.attempt_id == attempt_id)
        .order_by(ExamSecurityEvent.created_at.desc(), ExamSecurityEvent.id.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "event_type": r.event_type,
                "severity": r.severity,
                "verified": r.verified,
                "message": r.message,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "context": json.loads(r.context_json) if r.context_json else None,
            }
        )
    return out


def summarize_events(attempt_id: int) -> dict[str, Any]:
    db = get_db()
    rows = db.query(ExamSecurityEvent).filter(ExamSecurityEvent.attempt_id == attempt_id).all()
    by_type: dict[str, int] = {}
    high = 0
    for r in rows:
        by_type[r.event_type] = by_type.get(r.event_type, 0) + 1
        if r.severity in ("high", "critical"):
            high += 1
    return {"total": len(rows), "by_type": by_type, "high_or_critical": high}


def build_seb_config_dict(assessment: Assessment, start_url: str) -> dict[str, Any]:
    """Minimal SEB JSON settings for download (SEB 3.x JSON config family).

    Teachers should validate the downloaded config in the official SEB Config
    Tool and paste Browser Exam Keys when enforcing binary integrity.
    """
    ensure_seb_config_key(assessment)
    quit_link = urljoin(start_url if start_url.endswith("/") else start_url + "/", "../")
    return {
        "startURL": start_url,
        "examSessionClearCookiesOnStart": True,
        "examSessionClearCookiesOnEnd": True,
        "allowQuit": True,
        "quitURL": quit_link if getattr(assessment, "seb_quit_link_enabled", True) else "",
        "quitURLConfirm": True,
        "sendBrowserExamKey": True,
        "browserWindowAllowReload": True,
        "browserWindowShowURL": False,
        "enableBrowserWindowToolbar": False,
        "newBrowserWindowByLinkPolicy": 0,
        "urlFilterEnable": True,
        "urlFilterEnableContentFilter": True,
        "urlFilterRules": [
            {"action": 1, "active": True, "expression": start_url.split("?")[0] + "*", "regex": False},
            {"action": 1, "active": True, "expression": "*/api/lms/*", "regex": False},
        ],
        "iqbalaiConfigKeyHint": assessment.seb_config_key_hash,
        "iqbalaiAssessmentId": assessment.id,
        "iqbalaiNote": (
            "Open this file with Safe Exam Browser. "
            "Server verifies X-SafeExamBrowser-ConfigKeyHash / RequestHash. "
            "OS lockdown requires a genuine SEB client — not a normal browser."
        ),
    }


def update_security_settings(assessment: Assessment, body: dict, *, actor_id: int) -> Assessment:
    """Teacher/admin update of security knobs (server-enforced)."""
    if "security_mode" in body and body["security_mode"] is not None:
        mode = str(body["security_mode"]).strip().lower()
        if mode in _LEGACY_SEB_MODES:
            mode = "standard"
        if mode not in SECURITY_MODES:
            raise LMSValidationError("security_mode must be standard or supervised (in-app only; SEB removed)")
        assessment.security_mode = mode
    # Always clear third-party SEB requirement.
    assessment.require_seb = False
    if "require_seb" in body and bool(body["require_seb"]):
        logger.info(
            "Ignoring require_seb=true for assessment %s — third-party SEB is disabled",
            assessment.id,
        )
    if "allow_resume" in body:
        assessment.allow_resume = bool(body["allow_resume"])
    if "max_interruptions" in body:
        v = body["max_interruptions"]
        assessment.max_interruptions = None if v in (None, "", "null") else max(0, int(v))
    if "max_concurrent_sessions" in body:
        assessment.max_concurrent_sessions = max(1, int(body["max_concurrent_sessions"] or 1))
    if "auto_submit_on_expiry" in body:
        assessment.auto_submit_on_expiry = bool(body["auto_submit_on_expiry"])
    if "warn_on_focus_loss" in body:
        assessment.warn_on_focus_loss = bool(body["warn_on_focus_loss"])
    if "exam_window_start" in body:
        assessment.exam_window_start = _parse_dt(body["exam_window_start"])
    if "exam_window_end" in body:
        assessment.exam_window_end = _parse_dt(body["exam_window_end"])
    # Drop any stored third-party SEB material on settings save.
    assessment.seb_browser_exam_keys_json = None
    if body.get("rotate_seb_config_key") or "seb_browser_exam_keys" in body:
        assessment.seb_config_key_hash = None

    logger.info(
        "Assessment %s security updated by user %s → mode=%s require_seb=%s (in-app only)",
        assessment.id,
        actor_id,
        assessment.security_mode,
        assessment.require_seb,
    )
    db = get_db()
    db.commit()
    db.refresh(assessment)
    return assessment


def _parse_dt(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise LMSValidationError("Invalid datetime; use ISO-8601 UTC.") from exc


def current_user_is_owner(attempt: AssessmentAttempt) -> bool:
    try:
        return has_request_context() and session.get("user_id") == attempt.student_id
    except Exception:  # noqa: BLE001
        return False
