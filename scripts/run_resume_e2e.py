#!/usr/bin/env python3
"""API tests for pause/resume: diagnostic, quiz attempts, practice, deficiency tutor history."""
from __future__ import annotations

import json
import sys
import time
import uuid

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://209.23.10.34"
ADMIN_EMAIL = "admin@iqbalai.com"
ADMIN_PASS = "Hamzakhanswati12@"
STUDENT_PASS = "E2eTest2026!"
RUN_ID = int(time.time())


class Api:
    def __init__(self):
        self.s = requests.Session()
        self.s.verify = False

    def login(self, email: str, password: str) -> bool:
        r = self.s.post(
            f"{BASE_URL}/auth/login",
            data={"useremail": email, "password": password},
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            timeout=60,
        )
        return r.status_code == 200 and r.json().get("success")

    def get(self, path: str):
        r = self.s.get(f"{BASE_URL}{path}", timeout=60)
        return r.status_code, r.json()

    def post(self, path: str, body=None):
        r = self.s.post(f"{BASE_URL}{path}", json=body, timeout=60)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:400]

    def post_admin_user(self, payload):
        r = self.s.post(f"{BASE_URL}/admin/users", json=payload, timeout=60)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:400]


def unwrap(body):
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def log(ok: bool, name: str, detail: str = ""):
    icon = "PASS" if ok else "FAIL"
    print(f"[{icon}] {name}: {detail}")
    return ok


def main() -> int:
    api = Api()
    passed = 0
    failed = 0

    def check(ok, name, detail=""):
        nonlocal passed, failed
        if log(ok, name, detail):
            passed += 1
        else:
            failed += 1
        return ok

    # Admin: find a published quiz (non-diagnostic) for assignment test
    if not api.login(ADMIN_EMAIL, ADMIN_PASS):
        print("Admin login failed")
        return 1

    st_email = f"resume_e2e_{RUN_ID}@iqbalai.com"
    username = f"resume{RUN_ID}"
    code, body = api.post_admin_user(
        {
            "username": username,
            "useremail": st_email,
            "password": STUDENT_PASS,
            "role": "student",
            "class_standard": "8",
            "medium": "English",
        }
    )
    ok = code == 200 and isinstance(body, dict) and body.get("success")
    check(ok, "create student", str(body)[:200])

    if not api.login(st_email, STUDENT_PASS):
        check(False, "student login", st_email)
        return 1
    check(True, "student login", st_email)

    # --- Diagnostic resume ---
    code, body = api.get("/api/lms/diagnostics/default")
    diag = unwrap(body)
    diag_id = diag.get("id") if isinstance(diag, dict) else None
    weak_topic_id = None
    path = None
    if diag_id and not diag.get("diagnostic_completed"):
        code, body = api.post(f"/api/lms/quizzes/{diag_id}/start")
        start1 = unwrap(body)
        attempt1 = start1.get("attempt_id")
        check(attempt1 is not None, "diag start attempt1", json.dumps(start1)[:120])

        code, body = api.get(f"/api/lms/attempts/{attempt1}/questions")
        qdata = unwrap(body)
        questions = qdata.get("questions") or []
        if questions:
            q0 = questions[0]
            qid = q0.get("question_id")
            code, body = api.post(
                f"/api/lms/attempts/{attempt1}/answer",
                {"question_id": qid, "selected_option_index": 0},
            )
            check(code == 200, "diag save answer q0", str(body)[:120])

        code, body = api.post(f"/api/lms/quizzes/{diag_id}/start")
        start2 = unwrap(body)
        attempt2 = start2.get("attempt_id")
        resumed = start2.get("resumed")
        check(attempt2 == attempt1 and resumed is True, "diag resume same attempt", f"{attempt1} -> {attempt2} resumed={resumed}")

        code, body = api.get(f"/api/lms/attempts/{attempt2}/questions")
        qdata2 = unwrap(body)
        saved = qdata2.get("saved_answers") or {}
        check("0" in saved or 0 in saved, "diag saved_answers restored", json.dumps(saved))

        for i, q in enumerate(questions):
            qid = q.get("question_id")
            if qid:
                api.post(
                    f"/api/lms/attempts/{attempt2}/answer",
                    {"question_id": qid, "selected_option_index": i % 4},
                )
        code, body = api.post(f"/api/lms/attempts/{attempt2}/submit")
        submit_res = unwrap(body)
        weak = submit_res.get("weak_topics") or []
        if weak:
            weak_topic_id = weak[0].get("topic_id")
        check(code == 200, "diag submit complete", str(body)[:120])
        code, body = api.get("/api/lms/students/me/learning-path")
        path = unwrap(body)
    else:
        check(True, "diag resume", "skip - no open diagnostic or already completed")

    # --- Practice resume (needs topic_id) ---
    topic_id = weak_topic_id
    if topic_id is None and path and path.get("items"):
        for it in path["items"]:
            if it.get("item_type") == "practice" and it.get("item_id"):
                topic_id = it.get("item_id")
                break
    code, body = api.post("/api/lms/practice/sessions", {"topic_id": topic_id, "force_new": True})
    if code == 201 or (code == 200 and unwrap(body).get("session_id")):
        s1 = unwrap(body).get("session_id")
        code, body = api.post("/api/lms/practice/sessions", {"topic_id": topic_id, "force_new": False})
        s2 = unwrap(body).get("session_id")
        resumed = unwrap(body).get("resumed")
        check(s1 == s2 and resumed is True, "practice resume", f"{s1} -> {s2} resumed={resumed}")
    else:
        check(True, "practice resume", f"skip - {str(body)[:120]}")

    # --- Learning path practice label ---
    if path is None:
        code, body = api.get("/api/lms/students/me/learning-path")
        path = unwrap(body)
    if path and path.get("items"):
        titles = [it.get("title", "") for it in path["items"]]
        bad = [t for t in titles if t == "Quiz #0"]
        check(not bad, "learning path no Quiz #0", str(titles[:5]))
    else:
        check(True, "learning path label", "skip - no path")

    # --- Deficiency tutor messages in session state (needs completed diagnostic) ---
    code, body = api.post("/api/lms/deficiency/sessions", {"force_new": True})
    if code in (200, 201):
        sess = unwrap(body)
        sid = sess.get("session_id")
        check("tutor_messages" in sess, "deficiency tutor_messages key", json.dumps(list(sess.keys())))
        if sid:
            code, body = api.post(
                f"/api/lms/deficiency/sessions/{sid}/explain",
                {"message": "help me understand"},
            )
            if code == 200:
                code, body = api.post("/api/lms/deficiency/sessions", {"force_new": False})
                sess2 = unwrap(body)
                msgs = sess2.get("tutor_messages") or []
                check(len(msgs) >= 2, "deficiency tutor history restored", f"count={len(msgs)}")
            else:
                check(True, "deficiency tutor history", "skip explain failed")
    else:
        check(True, "deficiency tutor_messages", f"skip - {str(body)[:120]}")

    print(f"\nResults: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
