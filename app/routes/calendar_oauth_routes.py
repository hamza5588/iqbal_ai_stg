"""Google Calendar OAuth (offline refresh token) — credentials stored encrypted per user."""
from __future__ import annotations

import logging
import secrets

import requests
from flask import Blueprint, redirect, request, session, url_for, Response

from app.config import Config
from app.services.calendar_connection_service import save_google_oauth_tokens
from app.utils.auth import login_required
from app.utils.db import get_db

logger = logging.getLogger(__name__)

calendar_oauth_bp = Blueprint("calendar_oauth", __name__, url_prefix="/auth/calendar")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def _redirect_uri() -> str:
    uri = (Config.GOOGLE_CALENDAR_REDIRECT_URI or "").strip()
    if uri:
        return uri
    base = (Config.SERVER_URL or "").rstrip("/")
    if base:
        return f"{base}/auth/calendar/google/callback"
    return url_for("calendar_oauth.google_calendar_callback", _external=True)


def _client_config() -> dict:
    cid = (Config.GOOGLE_CALENDAR_CLIENT_ID or "").strip()
    sec = (Config.GOOGLE_CALENDAR_CLIENT_SECRET or "").strip()
    if not cid or not sec:
        raise RuntimeError("Google Calendar OAuth is not configured (GOOGLE_CALENDAR_CLIENT_ID / SECRET).")
    return {
        "web": {
            "client_id": cid,
            "client_secret": sec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_redirect_uri()],
        }
    }


def _flow():
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=_redirect_uri())


@calendar_oauth_bp.route("/google/start", methods=["GET"])
@login_required
def google_calendar_start():
    """Begin Google OAuth; user must be logged in so we attach tokens to their account."""
    try:
        flow = _flow()
        state = secrets.token_urlsafe(32)
        session["google_cal_oauth_state"] = state
        next_url = request.args.get("next") or url_for("phase2.student_learning_hub")
        session["calendar_oauth_next"] = next_url
        authorization_url = flow.authorization_url(
            state=state,
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return redirect(authorization_url)
    except Exception as exc:
        logger.exception("Google calendar OAuth start failed: %s", exc)
        return Response(
            (
                "Calendar connection unavailable. Configure GOOGLE_CALENDAR_CLIENT_ID, "
                "GOOGLE_CALENDAR_CLIENT_SECRET, and GOOGLE_CALENDAR_REDIRECT_URI in the server environment. "
                f"Detail: {exc}"
            ),
            status=503,
            mimetype="text/plain",
        )


@calendar_oauth_bp.route("/google/callback", methods=["GET"])
@login_required
def google_calendar_callback():
    if request.args.get("error"):
        err = request.args.get("error_description") or request.args.get("error")
        return redirect(url_for("phase2.student_learning_hub", calendar_error=err[:200]))

    state = session.pop("google_cal_oauth_state", None)
    if not state or state != request.args.get("state"):
        return "Invalid OAuth state — try connecting again.", 400

    next_url = session.pop("calendar_oauth_next", None) or url_for("phase2.student_learning_hub")

    try:
        flow = _flow()
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        email_hint = None
        try:
            if creds.token:
                r = requests.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {creds.token}"},
                    timeout=15,
                )
                if r.ok:
                    email_hint = r.json().get("email")
        except Exception as exc:
            logger.debug("userinfo fetch skipped: %s", exc)

        db = get_db()
        save_google_oauth_tokens(
            db,
            user_id=int(session["user_id"]),
            refresh_token=creds.refresh_token,
            token_uri=creds.token_uri or "https://oauth2.googleapis.com/token",
            scopes=list(creds.scopes or SCOPES),
            email_hint=email_hint,
        )
        return redirect(next_url)
    except Exception as exc:
        logger.exception("Google calendar OAuth callback failed: %s", exc)
        return redirect(url_for("phase2.student_learning_hub", calendar_error=str(exc)[:200]))
