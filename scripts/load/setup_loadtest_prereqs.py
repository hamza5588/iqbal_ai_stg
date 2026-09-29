#!/usr/bin/env python3
"""Verify prerequisites and create 200 load-test student accounts on staging."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://209.23.10.34"
ADMIN_EMAIL = "admin@iqbalai.com"
ADMIN_PASS = "Hamzakhanswati12@"
TEACHER_EMAIL = "loadtest_teacher@test.iqbalai.local"
TEACHER_PASS = "LoadTest2026!"
FALLBACK_TEACHER = "teacher@iqbalai.com"
FALLBACK_TEACHER_PASS = "password123"
STUDENT_PASSWORD = "LoadTest2026!"
NUM_STUDENTS = 200
OUT = Path(__file__).resolve().parents[2] / "_qa_audit_tmp" / "load_test"
OUT.mkdir(parents=True, exist_ok=True)


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
        return r.status_code == 200 and (r.json().get("success") if r.headers.get("content-type", "").startswith("application/json") else True)

    def get(self, path: str):
        r = self.s.get(f"{BASE_URL}{path}", timeout=60)
        return r.status_code, r.json() if "json" in r.headers.get("content-type", "") else r.text

    def post(self, path: str, body=None, form=None):
        if form:
            r = self.s.post(f"{BASE_URL}{path}", data=form, timeout=60)
        else:
            r = self.s.post(f"{BASE_URL}{path}", json=body, timeout=60)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:300]

    def put(self, path: str, body=None):
        r = self.s.put(f"{BASE_URL}{path}", json=body, timeout=60)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:300]


def unwrap(body):
    return body.get("data", body) if isinstance(body, dict) else body


def main() -> int:
    meta = {"base_url": BASE_URL, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    admin = Api()
    if not admin.login(ADMIN_EMAIL, ADMIN_PASS):
        print("FAIL: admin login")
        return 1
    print("OK: admin login")

    code, body = admin.get("/api/lms/admin/diagnostics")
    diags = unwrap(body) if code == 200 else []
    if not isinstance(diags, list):
        diags = [diags] if diags else []
    published = [d for d in diags if isinstance(d, dict) and d.get("status") == "published"]
    if not published:
        print("FAIL: no published diagnostic")
        return 1
    diag = published[0]
    meta["diagnostic_id"] = diag.get("id")
    print(f"OK: published diagnostic id={meta['diagnostic_id']}")

    teacher = Api()
    teacher_ok = teacher.login(TEACHER_EMAIL, TEACHER_PASS)
    if not teacher_ok:
        admin.post(
            "/admin/users",
            body={
                "username": "loadtest_teacher",
                "useremail": TEACHER_EMAIL,
                "password": TEACHER_PASS,
                "role": "teacher",
                "class_standard": "8",
                "medium": "English",
            },
        )
        teacher_ok = teacher.login(TEACHER_EMAIL, TEACHER_PASS)
    if not teacher_ok:
        teacher_ok = teacher.login(FALLBACK_TEACHER, FALLBACK_TEACHER_PASS)

    join_code = None
    assignment_id = None
    quiz_id = None
    if teacher_ok:
        print("OK: teacher login")
        code, body = teacher.get("/api/lms/classes/mine")
        classes = unwrap(body) if code == 200 else []
        if not classes:
            code, body = teacher.post(
                "/api/lms/classes",
                body={"name": "LoadTest Class 8", "description": "Load test", "grade_level": "8"},
            )
            classes = [unwrap(body)] if code in (200, 201) else []
        if classes:
            cls = classes[0]
            join_code = cls.get("join_code")
            cid = cls.get("id")
            code2, body2 = teacher.get(f"/api/lms/assignments?class_id={cid}")
            assigns = unwrap(body2) if code2 == 200 else []
            pub = [a for a in assigns if isinstance(a, dict)]
            if pub:
                assignment_id = pub[0].get("id")
                quiz_id = pub[0].get("quiz_id")
            if not quiz_id:
                code3, body3 = teacher.get("/api/lms/quizzes")
                quizzes = unwrap(body3) if code3 == 200 else []
                published_quizzes = [
                    q for q in quizzes if isinstance(q, dict) and q.get("status") == "published"
                ]
                if published_quizzes:
                    quiz_id = published_quizzes[0].get("id")
                if not quiz_id:
                    code_n, body_n = teacher.post(
                        "/api/lms/quizzes",
                        body={"title": "Load Test Quiz", "assessment_type": "quiz", "creation_mode": "manual"},
                    )
                    if code_n in (200, 201):
                        quiz_id = unwrap(body_n).get("id")
                        qids = []
                        for i in range(4):
                            cq, bq = teacher.post(
                                "/api/lms/questions",
                                body={
                                    "question_text": f"Load test question {i + 1}: What is 2 + 2?",
                                    "options": [
                                        {"label": "A", "text": "3"},
                                        {"label": "B", "text": "4"},
                                        {"label": "C", "text": "5"},
                                        {"label": "D", "text": "6"},
                                    ],
                                    "correct_option_index": 1,
                                    "difficulty": "easy",
                                },
                            )
                            if cq in (200, 201):
                                qids.append(unwrap(bq).get("id"))
                        if qids and quiz_id:
                            teacher.put(f"/api/lms/quizzes/{quiz_id}/questions", {"question_ids": qids})
                            teacher.post(f"/api/lms/quizzes/{quiz_id}/publish")
                if quiz_id and not assignment_id:
                    code4, body4 = teacher.post(
                        "/api/lms/assignments",
                        body={
                            "class_id": cid,
                            "quiz_id": quiz_id,
                            "title": "Load Test Quiz Assignment",
                        },
                    )
                    if code4 in (200, 201):
                        assignment_id = unwrap(body4).get("id")
                        teacher.post(f"/api/lms/assignments/{assignment_id}/publish")
    else:
        print("WARN: teacher login failed — quiz step may skip")

    meta["join_code"] = join_code
    meta["assignment_id"] = assignment_id
    meta["quiz_id"] = quiz_id
    print(f"OK: join_code={join_code} assignment_id={assignment_id} quiz_id={quiz_id}")

    created = 0
    existing = 0
    failed = 0
    emails = []
    for i in range(1, NUM_STUDENTS + 1):
        email = f"loadtest_student_{i:03d}@test.iqbalai.local"
        username = f"loadtest{i:03d}"
        code, body = admin.post(
            "/admin/users",
            body={
                "username": username,
                "useremail": email,
                "password": STUDENT_PASSWORD,
                "role": "student",
                "class_standard": "8",
                "medium": "English",
            },
        )
        if code == 200 and isinstance(body, dict) and body.get("success"):
            created += 1
        elif "already" in str(body).lower() or "exists" in str(body).lower():
            existing += 1
        else:
            failed += 1
            if failed <= 3:
                print(f"WARN create {email}: {body}")
        emails.append(email)

    print(f"Students: created={created} existing={existing} failed={failed}")

    join_code = meta.get("join_code")
    joined = 0
    if join_code:
        for email in emails:
            st = Api()
            if not st.login(email, STUDENT_PASSWORD):
                continue
            code, body = st.post("/api/lms/classes/join", body={"join_code": join_code})
            if code in (200, 201):
                joined += 1
            else:
                msg = str(body)
                if "already" in msg.lower():
                    joined += 1
        print(f"Class join: {joined}/{NUM_STUDENTS} students enrolled")

    meta.update(
        {
            "student_password": STUDENT_PASSWORD,
            "num_students": NUM_STUDENTS,
            "emails_pattern": "loadtest_student_{N:03d}@test.iqbalai.local",
        }
    )
    cfg_path = OUT / "loadtest_config.json"
    cfg_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {cfg_path}")
    return 0 if published else 1


if __name__ == "__main__":
    sys.exit(main())
