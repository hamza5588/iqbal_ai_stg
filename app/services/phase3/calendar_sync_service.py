"""Push study-plan days to Google Calendar and Apple CalDAV (two-way replace for plan dates)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from sqlalchemy.orm import Session

from app.config import Config
from app.models.phase3_models import StudentStudyPlan, UserCalendarConnection
from app.services.calendar_connection_service import (
    PROVIDER_APPLE_CALDAV,
    PROVIDER_GOOGLE_OAUTH,
    get_decrypted_apple_payload,
    get_decrypted_google_payload,
)

logger = logging.getLogger(__name__)


def _next_date_str(ds: str) -> str:
    d = datetime.strptime(ds[:10], "%Y-%m-%d").date()
    return (d + timedelta(days=1)).isoformat()


def _iter_plan_days(plan_blob: Dict[str, Any], plan_row_id: int) -> List[Tuple[str, str, str]]:
    """Return (stable_uid, iso_date, title)."""
    out: List[Tuple[str, str, str]] = []
    pid = str(plan_row_id)
    for si, sec in enumerate(plan_blob.get("sections") or []):
        for di, day in enumerate(sec.get("days") or []):
            ds = day.get("date")
            if not ds:
                continue
            ds_s = str(ds)[:10]
            title = (day.get("focus_title") or day.get("title") or "Study block").strip()
            uid = f"iqbal:{pid}:{si}:{di}:{ds_s}"
            out.append((uid, ds_s, title))
    return out


def _load_meta(row: UserCalendarConnection) -> Dict[str, Any]:
    if not row.sync_meta_json:
        return {}
    try:
        return json.loads(row.sync_meta_json)
    except Exception:
        return {}


def _save_meta(db: Session, row: UserCalendarConnection, meta: Dict[str, Any]) -> None:
    row.sync_meta_json = json.dumps(meta, default=str)
    db.commit()


def _google_credentials(payload: Dict[str, Any]):
    from google.oauth2.credentials import Credentials

    cid = (Config.GOOGLE_CALENDAR_CLIENT_ID or "").strip()
    sec = (Config.GOOGLE_CALENDAR_CLIENT_SECRET or "").strip()
    if not cid or not sec:
        raise RuntimeError(
            "Google OAuth client is not configured (GOOGLE_CALENDAR_CLIENT_ID / SECRET)."
        )
    return Credentials(
        token=None,
        refresh_token=payload.get("refresh_token"),
        token_uri=payload.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=cid,
        client_secret=sec,
        scopes=payload.get("scopes")
        or ["https://www.googleapis.com/auth/calendar.events"],
    )


def _sync_google(
    db: Session,
    conn_row: UserCalendarConnection,
    plan_row_id: int,
    days: List[Tuple[str, str, str]],
) -> Dict[str, Any]:
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    raw = get_decrypted_google_payload(db, user_id=conn_row.user_id)
    if not raw:
        return {"ok": False, "error": "no_google_tokens"}
    creds = _google_credentials(raw)
    creds.refresh(Request())
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    meta = _load_meta(conn_row)
    g_meta = meta.setdefault("google", {})
    by_uid: Dict[str, str] = g_meta.setdefault("by_uid", {})
    desired_uids = {u for u, _, _ in days}

    stale = set(by_uid.keys()) - desired_uids
    for old_uid in stale:
        eid = by_uid.pop(old_uid, None)
        if eid:
            try:
                service.events().delete(calendarId="primary", eventId=eid).execute()
            except Exception as exc:
                logger.warning("Google calendar delete %s failed: %s", eid, exc)

    created = 0
    updated = 0
    for uid, ds, title in days:
        body = {
            "summary": f"[Iqbal AI] {title}",
            "description": "Synced from your Iqbal AI study plan.",
            "start": {"date": ds},
            "end": {"date": _next_date_str(ds)},
            "extendedProperties": {"private": {"iqbal_uid": uid}},
        }
        existing_id = by_uid.get(uid)
        if existing_id:
            try:
                service.events().patch(
                    calendarId="primary", eventId=existing_id, body=body
                ).execute()
                updated += 1
                continue
            except Exception:
                by_uid.pop(uid, None)
        ins = service.events().insert(calendarId="primary", body=body).execute()
        eid = ins.get("id")
        if eid:
            by_uid[uid] = eid
        created += 1

    g_meta["by_uid"] = by_uid
    g_meta["last_plan_row_id"] = plan_row_id
    meta["google"] = g_meta
    _save_meta(db, conn_row, meta)
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "removed_stale": len(stale),
    }


def _pick_apple_calendar(principal):
    try:
        calendars = principal.calendars()
    except Exception as exc:
        raise RuntimeError(f"CalDAV calendar list failed: {exc}") from exc
    if not calendars:
        raise RuntimeError("No writable CalDAV calendars found for this Apple ID.")
    for c in calendars:
        name = (getattr(c, "name", None) or "").lower()
        if "calendar" in name or "home" in name:
            return c
    return calendars[0]


def _sync_apple(
    db: Session,
    conn_row: UserCalendarConnection,
    plan_row_id: int,
    days: List[Tuple[str, str, str]],
) -> Dict[str, Any]:
    try:
        import caldav  # type: ignore
    except ImportError:
        return {"ok": False, "error": "caldav package not installed"}

    ap = get_decrypted_apple_payload(db, user_id=conn_row.user_id)
    if not ap:
        return {"ok": False, "error": "no_apple_credentials"}

    url = (ap.get("caldav_host") or "https://caldav.icloud.com").rstrip("/") + "/"
    client = caldav.DAVClient(
        url=url,
        username=(ap.get("apple_id") or "").strip(),
        password=(ap.get("app_password") or "").strip(),
    )
    principal = client.principal()
    cal = _pick_apple_calendar(principal)

    meta = _load_meta(conn_row)
    a_meta = meta.setdefault("apple", {})
    by_uid: Dict[str, str] = a_meta.setdefault("href_by_uid", {})
    desired_uids = {u for u, _, _ in days}

    stale = set(by_uid.keys()) - desired_uids
    for old_uid in stale:
        href = by_uid.pop(old_uid, None)
        if href:
            try:
                ev = cal.event_by_url(href)
                ev.delete()
            except Exception as exc:
                logger.warning("CalDAV delete %s failed: %s", href, exc)

    created = 0
    for uid, ds, title in days:
        dt_start = ds.replace("-", "")
        dt_end = _next_date_str(ds).replace("-", "")
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "PRODID:-//IqbalAI//Phase3//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{uid}@iqbal.ai\r\n"
            f"DTSTART;VALUE=DATE:{dt_start}\r\n"
            f"DTEND;VALUE=DATE:{dt_end}\r\n"
            f"SUMMARY:[Iqbal AI] {title.replace(chr(10), ' ')[:900]}\r\n"
            "DESCRIPTION:Synced from your Iqbal AI study plan.\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        existing_href = by_uid.get(uid)
        if existing_href:
            try:
                ev = cal.event_by_url(existing_href)
                ev.delete()
            except Exception:
                by_uid.pop(uid, None)

        ev_obj = cal.save_event(ics)
        href = getattr(ev_obj, "url", None) or (str(ev_obj) if ev_obj is not None else "")
        if href:
            by_uid[uid] = href
        created += 1

    a_meta["href_by_uid"] = by_uid
    a_meta["last_plan_row_id"] = plan_row_id
    meta["apple"] = a_meta
    _save_meta(db, conn_row, meta)
    return {"ok": True, "created": created, "removed_stale": len(stale)}


def sync_user_calendars(db: Session, *, student_user_id: int) -> Dict[str, Any]:
    """Sync latest study plan to connected external calendars."""
    plan_row = (
        db.query(StudentStudyPlan)
        .filter(StudentStudyPlan.student_user_id == int(student_user_id))
        .order_by(StudentStudyPlan.updated_at.desc())
        .first()
    )
    if not plan_row:
        return {"ok": False, "error": "no_study_plan"}

    try:
        blob = json.loads(plan_row.plan_json)
    except Exception:
        blob = {}

    days = _iter_plan_days(blob, plan_row.id)
    out: Dict[str, Any] = {
        "ok": True,
        "plan_id": plan_row.id,
        "entries": len(days),
        "google": None,
        "apple": None,
    }

    g_row = (
        db.query(UserCalendarConnection)
        .filter(
            UserCalendarConnection.user_id == int(student_user_id),
            UserCalendarConnection.provider == PROVIDER_GOOGLE_OAUTH,
        )
        .first()
    )
    if g_row and get_decrypted_google_payload(db, user_id=student_user_id):
        try:
            out["google"] = _sync_google(db, g_row, plan_row.id, days)
        except Exception as exc:
            logger.exception("Google calendar sync failed")
            out["google"] = {"ok": False, "error": str(exc)}

    a_row = (
        db.query(UserCalendarConnection)
        .filter(
            UserCalendarConnection.user_id == int(student_user_id),
            UserCalendarConnection.provider == PROVIDER_APPLE_CALDAV,
        )
        .first()
    )
    if a_row and get_decrypted_apple_payload(db, user_id=student_user_id):
        try:
            out["apple"] = _sync_apple(db, a_row, plan_row.id, days)
        except Exception as exc:
            logger.exception("Apple CalDAV sync failed")
            out["apple"] = {"ok": False, "error": str(exc)}

    return out
