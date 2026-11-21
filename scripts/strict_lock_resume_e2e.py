#!/usr/bin/env python3
"""Playwright E2E: Strict lock survives refresh/resume; only teacher unlock clears it."""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Prefer a browsers path that already has Chromium (sandbox cache churn breaks default).
def _ensure_playwright_browsers_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    candidates = [
        ROOT / ".playwright-browsers",
        Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright",
    ]
    # Reuse any prior Cursor sandbox Playwright install that still has headless shell.
    cache_root = Path(tempfile.gettempdir()) / "cursor-sandbox-cache"
    if cache_root.is_dir():
        for shell in cache_root.glob("*/playwright/chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"):
            candidates.insert(0, shell.parents[2])  # .../playwright
            break
    for c in candidates:
        if c and Path(c).is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(c)
            return


_ensure_playwright_browsers_path()

EVIDENCE = ROOT / "docs" / "qa_evidence"
EVIDENCE.mkdir(parents=True, exist_ok=True)
VIDEO_DIR = EVIDENCE / "playwright_videos"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

TEACHER_EMAIL = "e2e.strict.resume.t@iqbalai.test"
STUDENT_EMAIL = "e2e.strict.resume.s@iqbalai.test"
PASSWORD = "StrictResumeE2E!2026"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    db_path = Path(tempfile.gettempdir()) / f"strict_resume_e2e_{int(time.time())}.db"
    os.environ["DATABASE_URL"] = "sqlite:///" + str(db_path).replace("\\", "/")
    os.environ["SKIP_EXTRA_STARTUP"] = "true"
    os.environ["FLASK_ENV"] = "development"
    os.environ["ENV"] = "local"
    os.environ.setdefault("SECRET_KEY", "strict-resume-e2e-secret")
    for k in ("POSTGRES_HOST", "MYSQL_HOST"):
        os.environ.pop(k, None)

    from app import create_app
    from app.models.database_models import User
    from app.models.lms_models import Assessment, AssessmentQuestion, Question, Topic
    from app.utils.db import get_db
    from app.utils.passwords import hash_password
    from werkzeug.serving import make_server

    port = _free_port()
    app = create_app()
    with app.app_context():
        db = get_db()
        for email in (TEACHER_EMAIL, STUDENT_EMAIL):
            row = db.query(User).filter(User.useremail == email).first()
            if row:
                db.delete(row)
        db.commit()
        teacher = User(
            username="e2e_strict_rt",
            full_name="E2E Strict Resume Teacher",
            school_name="E2E",
            useremail=TEACHER_EMAIL,
            password=hash_password(PASSWORD),
            role="teacher",
            class_standard="9",
            medium="English",
            groq_api_key="x",
            is_active=True,
        )
        student = User(
            username="e2e_strict_rs",
            full_name="E2E Strict Resume Student",
            school_name="E2E",
            useremail=STUDENT_EMAIL,
            password=hash_password(PASSWORD),
            role="student",
            class_standard="9",
            medium="English",
            groq_api_key="x",
            is_active=True,
        )
        topic = Topic(
            name="Strict Resume E2E",
            slug=f"strict-resume-{int(time.time())}",
            subject="Mathematics",
            grade_level="9",
        )
        db.add_all([teacher, student, topic])
        db.flush()
        q = Question(
            topic_id=topic.id,
            question_text="Resume E2E 2+2?",
            options_json=json.dumps(
                [
                    {"label": "A", "text": "3"},
                    {"label": "B", "text": "4"},
                    {"label": "C", "text": "5"},
                    {"label": "D", "text": "6"},
                ]
            ),
            correct_option_index=1,
            difficulty="easy",
            source_type="manual",
        )
        a = Assessment(
            title="Strict Lock Resume E2E",
            assessment_type="quiz",
            creation_mode="manual",
            status="published",
            created_by=teacher.id,
            time_limit_minutes=20,
            security_mode="strict",
            require_seb=False,
            allow_resume=True,
            warn_on_focus_loss=True,
            auto_submit_on_expiry=True,
        )
        db.add_all([q, a])
        db.flush()
        db.add(AssessmentQuestion(assessment_id=a.id, question_id=q.id, sort_order=1))
        db.commit()
        quiz_id = a.id
        teacher_id = teacher.id

    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    time.sleep(0.6)

    import app.services.lms.assignment_service as asvc

    asvc.resolve_student_quiz_assignment = lambda *args, **kwargs: None

    from playwright.sync_api import sync_playwright

    report = {"pass": False, "scenarios": {}, "base_url": base, "quiz_id": quiz_id}

    def login(page, email):
        page.goto(f"{base}/auth/login", wait_until="domcontentloaded")
        page.fill('input[name="useremail"]', email)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle")

    def start_quiz(page):
        return page.evaluate(
            """async (quizId) => {
              const res = await fetch('/api/lms/quizzes/' + quizId + '/start', {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({})
              });
              const body = await res.json();
              return { ok: res.ok, status: res.status, body };
            }""",
            quiz_id,
        )

    def apply_start(page, data):
        page.goto(f"{base}/student-dashboard", wait_until="domcontentloaded")
        page.wait_for_function("typeof window.lmsExamSecurityStart === 'function'", timeout=60000)
        page.evaluate(
            """(d) => {
              const sec = d.security || {};
              const locked = !!(d.is_locked || d.status === 'locked' || d.locked);
              window.lmsExamSecurityStart({
                attemptId: d.attempt_id,
                sessionToken: d.session_token,
                security: sec,
                requestFullscreen: false,
                locked: locked,
                is_locked: locked,
                status: d.status,
                lock_reason: d.lock_reason,
              });
            }""",
            data,
        )

    def lock_via_fullscreen_exit(page, attempt_id):
        return page.evaluate(
            """async (attemptId) => {
              const res = await fetch('/api/lms/attempts/' + attemptId + '/security-events', {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                  event_type: 'fullscreen_exit', severity: 'warn',
                  message: 'e2e fullscreen exit', context: {}
                })
              });
              const body = await res.json();
              return { ok: res.ok, status: res.status, body };
            }""",
            attempt_id,
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            record_video_dir=str(VIDEO_DIR),
            viewport={"width": 1280, "height": 720},
        )
        page = context.new_page()
        try:
            # --- Scenario 1: exit fullscreen → lock → refresh → resume → still locked ---
            login(page, STUDENT_EMAIL)
            start = start_quiz(page)
            assert start.get("ok"), start
            data = (start.get("body") or {}).get("data") or {}
            attempt_id = data["attempt_id"]
            token = data["session_token"]
            apply_start(page, data)
            ev = lock_via_fullscreen_exit(page, attempt_id) or {}
            ev_data = ((ev.get("body") or {}).get("data") or (ev.get("body") or {}))
            assert ev.get("ok"), ev
            assert ev_data.get("status") == "locked" or ev_data.get("is_locked") is True, ev
            page.evaluate(
                """(d) => {
                  window.lmsExamSecurityApplyServerLockState(d);
                }""",
                ev_data,
            )
            assert page.evaluate("() => window.lmsExamSecurityIsLocked()") is True

            # Refresh + resume (new page load)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("typeof window.lmsExamSecurityStart === 'function'", timeout=60000)
            resume = start_quiz(page)
            assert resume.get("ok"), resume
            rdata = (resume.get("body") or {}).get("data") or {}
            assert rdata.get("attempt_id") == attempt_id
            assert rdata.get("status") == "locked" and rdata.get("is_locked") is True
            apply_start(page, rdata)
            page.wait_for_timeout(200)
            # Stale in_progress must not unlock
            page.evaluate(
                """(tok) => {
                  window.lmsExamSecurityApplyServerLockState({
                    status: 'in_progress', is_locked: false,
                    remaining_seconds: 800, session_token: tok
                  });
                }""",
                rdata.get("session_token") or token,
            )
            still = page.evaluate("() => window.lmsExamSecurityIsLocked()")
            hb = page.evaluate(
                """async ({ attemptId, token }) => {
                  const res = await fetch('/api/lms/attempts/' + attemptId + '/heartbeat', {
                    method: 'POST', credentials: 'include',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ session_token: token })
                  });
                  const body = await res.json();
                  const d = body.data || body;
                  window.lmsExamSecurityApplyServerLockState(d);
                  return { status: d.status, is_locked: d.is_locked,
                           client_locked: window.lmsExamSecurityIsLocked() };
                }""",
                {"attemptId": attempt_id, "token": rdata.get("session_token")},
            )
            report["scenarios"]["refresh_resume_stays_locked"] = {
                "pass": still is True and hb.get("status") == "locked" and hb.get("client_locked") is True,
                "still_locked_after_stale": still,
                "heartbeat": hb,
                "resume_status": rdata.get("status"),
            }
            assert still is True
            assert hb.get("client_locked") is True

            # --- Scenario 2: close context (tab) → reopen → resume → still locked ---
            context.close()
            context = browser.new_context(
                record_video_dir=str(VIDEO_DIR),
                viewport={"width": 1280, "height": 720},
            )
            page = context.new_page()
            login(page, STUDENT_EMAIL)
            reopen = start_quiz(page)
            assert reopen.get("ok"), reopen
            odata = (reopen.get("body") or {}).get("data") or {}
            assert odata.get("attempt_id") == attempt_id
            assert odata.get("status") == "locked" and odata.get("is_locked") is True
            apply_start(page, odata)
            page.wait_for_timeout(200)
            client_locked = page.evaluate("() => window.lmsExamSecurityIsLocked()")
            report["scenarios"]["close_reopen_stays_locked"] = {
                "pass": client_locked is True and odata.get("status") == "locked",
                "client_locked": client_locked,
                "status": odata.get("status"),
            }
            assert client_locked is True

            # --- Scenario 3: teacher unlock → resume → unlocked ---
            # Teacher unlock via API (login as teacher in same browser would wipe student cookie;
            # use server-side unlock through flask app context).
            with app.app_context():
                from app.services.lms import exam_security_service as ess
                from app.services.lms import attempt_service as asvc2

                att = asvc2.get_attempt(attempt_id)
                ess.unlock_attempt(att, teacher_id=teacher_id, message="e2e teacher unlock")

            after_unlock = start_quiz(page)
            assert after_unlock.get("ok"), after_unlock
            udata = (after_unlock.get("body") or {}).get("data") or {}
            assert udata.get("status") == "in_progress"
            assert udata.get("is_locked") is False
            apply_start(page, udata)
            page.wait_for_timeout(200)
            unlocked_client = page.evaluate("() => window.lmsExamSecurityIsLocked()")
            # Explicit teacher unlock flag path
            page.evaluate(
                """(d) => {
                  window.lmsExamSecurityStart({
                    attemptId: d.attempt_id, sessionToken: d.session_token,
                    security: d.security || {}, requestFullscreen: false,
                    locked: true, status: 'locked'
                  });
                  window.lmsExamSecurityApplyServerLockState({
                    status: 'in_progress', is_locked: false, unlocked: true,
                    session_token: d.session_token
                  });
                }""",
                udata,
            )
            after_flag = page.evaluate("() => window.lmsExamSecurityIsLocked()")
            report["scenarios"]["teacher_unlock_then_resume"] = {
                "pass": unlocked_client is False and after_flag is False and udata.get("status") == "in_progress",
                "resume_unlocked": unlocked_client is False,
                "flag_unlock": after_flag is False,
                "status": udata.get("status"),
            }
            assert unlocked_client is False
            assert after_flag is False

            report["pass"] = all(s.get("pass") for s in report["scenarios"].values())
        except Exception as exc:  # noqa: BLE001
            report["pass"] = False
            report["error"] = str(exc)
            raise
        finally:
            context.close()
            browser.close()
            vids = sorted(VIDEO_DIR.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
            if vids:
                report["video"] = str(vids[0])

    out = EVIDENCE / "strict_lock_resume_e2e.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    server.shutdown()
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
