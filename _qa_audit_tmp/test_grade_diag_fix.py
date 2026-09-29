#!/usr/bin/env python3
"""Verify grade-scoped diagnostic start fix + real Math IX PDF upload on staging."""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://nginx"
ADMIN_EMAIL = "admin@iqbalai.com"
ADMIN_PASS = "Hamzakhanswati12@"
STUDENT_PASS = "FixTest2026!"
RUN = int(time.time())

# Real PDFs already on the staging host (copied into cwd when run remotely)
DIAG_PDF = Path("diagnostic_qa_math_ix_sindh.pdf")
TARGET_PDF = Path("target_content_math_ix_sindh.pdf")
# Fallback grade-8 quadratic sample
DIAG_PDF_G8 = Path("diagnostic_qa_quadratic_equations_grade8.pdf")
TARGET_PDF_G8 = Path("target_content_quadratic_equations_grade8.pdf")


class Api:
    def __init__(self):
        self.s = requests.Session()
        self.s.verify = False

    def login(self, email: str, password: str) -> bool:
        r = self.s.post(
            f"{BASE}/auth/login",
            data={"useremail": email, "password": password},
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            timeout=60,
        )
        try:
            return r.status_code == 200 and bool(r.json().get("success"))
        except Exception:
            return False

    def get(self, path: str, timeout: int = 60):
        r = self.s.get(f"{BASE}{path}", timeout=timeout)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text[:300]}

    def post_json(self, path: str, body=None, timeout: int = 60):
        r = self.s.post(f"{BASE}{path}", json=body or {}, timeout=timeout)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text[:300]}

    def delete(self, path: str):
        r = self.s.delete(f"{BASE}{path}", timeout=60)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text[:300]}


def unwrap(body):
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def ok(label: str, cond: bool, detail: str = ""):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def create_student(admin: Api, grade: str) -> tuple[str, str]:
    email = f"fix.g{grade}.{RUN}@iqbalai.com"
    username = f"fix_g{grade}_{RUN}"
    code, body = admin.post_json(
        "/admin/users",
        {
            "username": username,
            "useremail": email,
            "password": STUDENT_PASS,
            "role": "student",
            "class_standard": grade,
            "medium": "English",
        },
    )
    if code not in (200, 201) or not (isinstance(body, dict) and body.get("success")):
        raise RuntimeError(f"create student grade {grade}: HTTP {code} {body}")
    return email, grade


def student_start_flow(email: str, grade: str) -> bool:
    api = Api()
    if not api.login(email, STUDENT_PASS):
        return ok(f"login grade {grade}", False, email)
    code, body = api.get("/api/lms/diagnostics/default")
    data = unwrap(body) if code == 200 else body
    if code != 200 or not isinstance(data, dict) or not data.get("id"):
        return ok(f"GET default grade {grade}", False, f"HTTP {code} {body}")
    diag_id = data["id"]
    ok(f"GET default grade {grade}", True, f"id={diag_id} title={data.get('title')} g={data.get('grade_level')}")

    code, start = api.post_json(f"/api/lms/quizzes/{diag_id}/start", {"retake": False})
    start_data = unwrap(start) if isinstance(start, dict) else start
    if code not in (200, 201) or not isinstance(start_data, dict) or not start_data.get("attempt_id"):
        err = start.get("error") if isinstance(start, dict) else start
        return ok(f"START grade {grade}", False, f"HTTP {code} {err} body={start}")
    attempt_id = start_data["attempt_id"]
    ok(f"START grade {grade}", True, f"attempt_id={attempt_id} HTTP {code}")

    code, qs = api.get(f"/api/lms/attempts/{attempt_id}/questions")
    qdata = unwrap(qs) if code == 200 else qs
    questions = (qdata or {}).get("questions") if isinstance(qdata, dict) else None
    if questions is None and isinstance(qdata, list):
        questions = qdata
    n = len(questions or [])
    return ok(f"QUESTIONS grade {grade}", n > 0, f"count={n}")


def poll_progress(admin: Api, job_id: str, timeout: int = 900) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, body = admin.get(f"/api/lms/diagnostics/upload-progress/{job_id}")
        last = unwrap(body) if code == 200 else {}
        if last.get("done") or last.get("error"):
            return last
        time.sleep(4)
    return {"error": "timeout", **last}


def real_pdf_flow(admin: Api, grade: str = "7") -> bool:
    print(f"\n=== Real PDF upload for grade {grade} ===")
    diag_path = DIAG_PDF if DIAG_PDF.is_file() else DIAG_PDF_G8
    tgt_path = TARGET_PDF if TARGET_PDF.is_file() else TARGET_PDF_G8
    if not diag_path.is_file() or not tgt_path.is_file():
        return ok("real PDF files present", False, f"missing {diag_path} / {tgt_path}")

    ok("using PDFs", True, f"{diag_path.name} + {tgt_path.name}")

    code, body = admin.get("/api/lms/admin/diagnostics")
    items = unwrap(body) if code == 200 else []
    for d in items or []:
        if str(d.get("grade_level")) == str(grade) and d.get("status") in ("published", "draft"):
            admin.delete(f"/api/lms/admin/diagnostics/{d['id']}")
            ok(f"archived old grade {grade} diagnostic", True, f"id={d['id']}")

    job_id = str(uuid.uuid4())
    files = [
        ("title", (None, f"Math IX FixTest grade {grade} {RUN}")),
        ("grade_level", (None, grade)),
        ("progress_job_id", (None, job_id)),
        ("diagnostic_file", (diag_path.name, diag_path.read_bytes(), "application/pdf")),
        ("target_files", (tgt_path.name, tgt_path.read_bytes(), "application/pdf")),
    ]
    r = admin.s.post(f"{BASE}/api/lms/diagnostics/from-pdf", files=files, timeout=180)
    try:
        upload_body = r.json()
    except Exception:
        upload_body = {"raw": r.text[:300]}
    if r.status_code not in (200, 201):
        return ok("upload real PDF", False, f"HTTP {r.status_code} {upload_body}")
    ok("upload accepted", True, f"HTTP {r.status_code}")

    prog = poll_progress(admin, job_id)
    if not (prog.get("done") and not prog.get("error")):
        return ok("PDF processing", False, str(prog)[:400])
    ok("PDF processing", True, prog.get("message", "done"))

    data = unwrap(upload_body) or {}
    aid = data.get("assessment_id") or data.get("id")
    if not aid:
        # progress may carry it
        aid = prog.get("assessment_id")
    if not aid:
        code, body = admin.get("/api/lms/admin/diagnostics")
        for d in unwrap(body) or []:
            if str(d.get("grade_level")) == str(grade) and d.get("status") == "draft":
                aid = d["id"]
                break
    if not aid:
        return ok("resolve assessment_id", False, str(upload_body)[:300])

    code, pub = admin.post_json(f"/api/lms/diagnostics/{aid}/publish", {})
    pub_data = unwrap(pub) if code == 200 else pub
    published = code == 200 and (pub_data or {}).get("status") == "published"
    if not published:
        return ok("publish", False, f"HTTP {code} {pub_data}")
    ok("publish", True, f"id={aid}")

    email, _ = create_student(admin, grade)
    return student_start_flow(email, grade)


def main() -> int:
    print(f"BASE={BASE} RUN={RUN}")
    admin = Api()
    if not admin.login(ADMIN_EMAIL, ADMIN_PASS):
        print("FAIL admin login")
        return 1
    print("  [PASS] admin login")

    print("\n=== Start existing grade diagnostics (regression) ===")
    all_ok = True
    for grade in ("6", "8", "9", "10"):
        try:
            email, g = create_student(admin, grade)
            if not student_start_flow(email, g):
                all_ok = False
        except Exception as e:
            ok(f"grade {grade} setup", False, str(e))
            all_ok = False

    # Real PDF replaces grade 7 and exercises full pipeline
    try:
        if not real_pdf_flow(admin, grade="7"):
            all_ok = False
    except Exception as e:
        ok("real PDF flow", False, str(e))
        all_ok = False

    print("\n=== SUMMARY ===")
    print("ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
