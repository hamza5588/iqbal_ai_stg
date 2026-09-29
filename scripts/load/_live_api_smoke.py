#!/usr/bin/env python3
"""Live staging smoke: health, login, dashboard, Learning Chat stay/advance."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

BASE = "https://209.23.10.34"
EMAIL = "loadtest_student_001@test.iqbalai.local"
PASSWORD = "LoadTest2026!"
CTX = ssl._create_unverified_context()


class Client:
    def __init__(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=CTX),
            urllib.request.HTTPCookieProcessor(self.jar),
        )

    def request(self, method: str, path: str, body=None, form=False, timeout=180):
        data = None
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        if form:
            data = urllib.parse.urlencode(body or {}).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode(errors="replace")
                return resp.status, raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            return e.code, raw

    def json(self, method: str, path: str, body=None, form=False, timeout=180):
        status, raw = self.request(method, path, body=body, form=form, timeout=timeout)
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"_raw": raw[:400]}
        data = parsed.get("data", parsed) if isinstance(parsed, dict) else parsed
        print(f"{method} {path} -> {status}", flush=True)
        return status, data, parsed


def unwrap_q(data: dict) -> dict:
    return data.get("current_question") or {}


def main() -> int:
    c = Client()
    st, raw = c.request("GET", "/health", timeout=30)
    print(f"GET /health -> {st} {raw[:80]!r}", flush=True)
    if st != 200:
        print("HEALTH_FAIL", flush=True)
        return 1

    st, data, _ = c.json("GET", "/api/lms/health", timeout=30)
    if st != 200:
        print("LMS_HEALTH_FAIL", data, flush=True)
        return 1
    print("lms_health", data, flush=True)

    st, data, parsed = c.json(
        "POST",
        "/auth/login",
        {"useremail": EMAIL, "password": PASSWORD},
        form=True,
        timeout=60,
    )
    if st != 200 or not (parsed.get("success") or data.get("success")):
        print("LOGIN_FAIL", parsed, flush=True)
        return 1
    print("login_ok", parsed.get("redirect_url") or data.get("redirect_url"), flush=True)

    st, data, _ = c.json("GET", "/api/lms/students/me/dashboard", timeout=60)
    if st != 200:
        print("DASH_FAIL", data, flush=True)
        return 1
    print("dashboard_ok", flush=True)

    st, data, _ = c.json("POST", "/api/lms/deficiency/sessions", {"force_new": True}, timeout=180)
    if st not in (200, 201) or not data.get("session_id"):
        print("CHAT_START_FAIL", data, flush=True)
        return 1
    sid = data["session_id"]
    idx0 = data.get("current_index")
    total = data.get("total_questions")
    print(f"chat_start session={sid} index={idx0} total={total}", flush=True)
    if not unwrap_q(data):
        print("CHAT_NO_QUESTION", data, flush=True)
        return 1

    wrong_seen = False
    stay_ok = False
    second_wrong_stay = False
    advance_ok = False

    for attempt in range(8):
        q = unwrap_q(data)
        nopts = len(q.get("options") or [0, 1, 2, 3])
        pick = attempt % max(nopts, 1)
        before = data.get("current_index")
        st, data, _ = c.json(
            "POST",
            f"/api/lms/deficiency/sessions/{sid}/answer",
            {"selected_option_index": pick},
            timeout=90,
        )
        if st != 200:
            print("ANSWER_FAIL", data, flush=True)
            return 1
        last = data.get("last_answer") or {}
        print(
            f"answer pick={pick} correct={last.get('correct')} stay={last.get('stay_on_question')} "
            f"index={data.get('current_index')} status={data.get('status')}",
            flush=True,
        )
        if last.get("correct") is True:
            continue
        wrong_seen = True
        stay_ok = last.get("stay_on_question") is True and data.get("current_index") == before
        st, data, _ = c.json(
            "POST",
            f"/api/lms/deficiency/sessions/{sid}/answer",
            {"selected_option_index": pick},
            timeout=90,
        )
        if st != 200:
            print("SECOND_WRONG_FAIL", data, flush=True)
            return 1
        last2 = data.get("last_answer") or {}
        second_wrong_stay = (
            last2.get("correct") is False
            and last2.get("stay_on_question") is True
            and data.get("current_index") == before
        )
        print(
            f"second_wrong correct={last2.get('correct')} stay={last2.get('stay_on_question')} "
            f"index={data.get('current_index')}",
            flush=True,
        )
        st, data, _ = c.json(
            "POST",
            f"/api/lms/deficiency/sessions/{sid}/advance",
            {},
            timeout=60,
        )
        if st != 200:
            print("ADVANCE_FAIL", data, flush=True)
            return 1
        msgs = data.get("tutor_messages") or []
        advance_ok = data.get("current_index") == before + 1 and msgs == []
        print(
            f"advance index={data.get('current_index')} tutor_msgs={len(msgs)} status={data.get('status')}",
            flush=True,
        )
        break

    print(
        json.dumps(
            {
                "wrong_seen": wrong_seen,
                "stay_ok": stay_ok,
                "second_wrong_stay": second_wrong_stay,
                "advance_ok": advance_ok,
            }
        ),
        flush=True,
    )
    if not (wrong_seen and stay_ok and second_wrong_stay and advance_ok):
        print("CHAT_BEHAVIOR_FAIL", flush=True)
        return 1
    print("LIVE_API_SMOKE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
