#!/usr/bin/env python3
"""Playwright E2E: Strict Mode lock must survive Continue / stale in_progress heartbeat."""

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

EVIDENCE = ROOT / "docs" / "qa_evidence"
EVIDENCE.mkdir(parents=True, exist_ok=True)
VIDEO_DIR = EVIDENCE / "playwright_videos"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

TEACHER_EMAIL = "e2e.strict.lock@iqbalai.test"
STUDENT_EMAIL = "e2e.strict.student@iqbalai.test"
PASSWORD = "StrictLockE2E!2026"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    db_path = Path(tempfile.gettempdir()) / f"strict_lock_e2e_{int(time.time())}.db"
    os.environ["DATABASE_URL"] = "sqlite:///" + str(db_path).replace("\\", "/")
    os.environ["SKIP_EXTRA_STARTUP"] = "true"
    os.environ["FLASK_ENV"] = "development"
    os.environ["ENV"] = "local"
    os.environ.setdefault("SECRET_KEY", "strict-lock-e2e-secret")
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
            username="e2e_strict_t",
            full_name="E2E Strict Teacher",
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
            username="e2e_strict_s",
            full_name="E2E Strict Student",
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
            name="Strict E2E",
            slug=f"strict-e2e-{int(time.time())}",
            subject="Mathematics",
            grade_level="9",
        )
        db.add_all([teacher, student, topic])
        db.flush()
        q = Question(
            topic_id=topic.id,
            question_text="Strict E2E 1+1?",
            options_json=json.dumps(
                [
                    {"label": "A", "text": "1"},
                    {"label": "B", "text": "2"},
                    {"label": "C", "text": "3"},
                    {"label": "D", "text": "4"},
                ]
            ),
            correct_option_index=1,
            difficulty="easy",
            source_type="manual",
        )
        a = Assessment(
            title="Strict Lock Continue E2E",
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
        student_id = student.id

    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    time.sleep(0.6)

    # Bypass assignment gate for start_attempt in this process
    import app.services.lms.assignment_service as asvc

    asvc.resolve_student_quiz_assignment = lambda *args, **kwargs: None

    from playwright.sync_api import sync_playwright

    report = {"pass": False, "steps": [], "base_url": base, "quiz_id": quiz_id}
    video_path = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            record_video_dir=str(VIDEO_DIR),
            viewport={"width": 1280, "height": 720},
        )
        page = context.new_page()
        try:
            page.goto(f"{base}/auth/login", wait_until="domcontentloaded")
            page.fill('input[name="useremail"]', STUDENT_EMAIL)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle")
            report["steps"].append({"login": True})

            # Start strict attempt via API in browser session
            start = page.evaluate(
                """async (quizId) => {
                  const res = await fetch('/api/lms/quizzes/' + quizId + '/start', {
                    method: 'POST',
                    credentials: 'include',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({})
                  });
                  const body = await res.json();
                  return { ok: res.ok, status: res.status, body };
                }""",
                quiz_id,
            )
            report["steps"].append({"start": start})
            assert start.get("ok"), start
            data = (start.get("body") or {}).get("data") or start.get("body") or {}
            attempt_id = data["attempt_id"]
            token = data.get("session_token")
            security = data.get("security") or {}
            assert security.get("security_mode") == "strict" or security.get("is_strict")

            # Load a page that has exam-security JS (student base)
            page.goto(f"{base}/student/dashboard", wait_until="domcontentloaded")
            page.wait_for_function("typeof window.lmsExamSecurityStart === 'function'")

            page.evaluate(
                """({ attemptId, token, security }) => {
                  window.lmsExamSecurityStart({
                    attemptId: attemptId,
                    sessionToken: token,
                    security: security,
                    requestFullscreen: false,
                    locked: false,
                  });
                }""",
                {"attemptId": attempt_id, "token": token, "security": security},
            )

            # Cross / minimize: fire leave signals (fail-closed local lock)
            page.evaluate(
                """() => {
                  Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
                  document.dispatchEvent(new Event('visibilitychange'));
                  window.dispatchEvent(new Event('blur'));
                }"""
            )
            page.wait_for_timeout(400)
            locked_after_cross = page.evaluate("() => window.lmsExamSecurityIsLocked()")
            report["steps"].append({"locked_after_cross": locked_after_cross})
            assert locked_after_cross is True

            # Server-side lock via security-events
            ev = page.evaluate(
                """async (attemptId) => {
                  const res = await fetch('/api/lms/attempts/' + attemptId + '/security-events', {
                    method: 'POST', credentials: 'include',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ event_type: 'tab_hidden', severity: 'warn',
                      message: 'e2e cross', context: {} })
                  });
                  return { ok: res.ok, body: await res.json() };
                }""",
                attempt_id,
            )
            report["steps"].append({"security_event": ev})

            # Continue: return focus + inject STALE in_progress heartbeat (same token)
            page.evaluate(
                """(token) => {
                  Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
                  document.dispatchEvent(new Event('visibilitychange'));
                  window.dispatchEvent(new Event('focus'));
                  // Simulate Continue / stale heartbeat that previously cleared the lock
                  window.lmsExamSecurityApplyServerLockState({
                    status: 'in_progress',
                    is_locked: false,
                    remaining_seconds: 900,
                    session_token: token,
                  });
                }""",
                token,
            )
            page.wait_for_timeout(200)
            still_locked = page.evaluate("() => window.lmsExamSecurityIsLocked()")
            report["steps"].append({"locked_after_continue_stale_hb": still_locked})
            assert still_locked is True, "Lock cleared after Continue/stale in_progress heartbeat"

            # Authoritative heartbeat while server locked
            hb = page.evaluate(
                """async ({ attemptId, token }) => {
                  const res = await fetch('/api/lms/attempts/' + attemptId + '/heartbeat', {
                    method: 'POST', credentials: 'include',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ session_token: token })
                  });
                  const body = await res.json();
                  const d = body.data || body;
                  if (window.lmsExamSecurityApplyServerLockState) {
                    window.lmsExamSecurityApplyServerLockState(d);
                  }
                  return { ok: res.ok, status: d.status, is_locked: d.is_locked,
                           client_locked: window.lmsExamSecurityIsLocked() };
                }""",
                {"attemptId": attempt_id, "token": token},
            )
            report["steps"].append({"heartbeat": hb})
            assert hb.get("status") == "locked" and hb.get("is_locked") is True
            assert hb.get("client_locked") is True

            overlay = page.locator("#lmsExamLockedOverlay")
            report["steps"].append({"overlay_visible": overlay.count() > 0 and overlay.is_visible()})
            report["pass"] = True
        except Exception as exc:  # noqa: BLE001
            report["pass"] = False
            report["error"] = str(exc)
            raise
        finally:
            context.close()
            browser.close()
            # Playwright moves video on context close
            vids = sorted(VIDEO_DIR.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
            if vids:
                video_path = str(vids[0])
                report["video"] = video_path

    out = EVIDENCE / "strict_lock_continue_e2e.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    server.shutdown()
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
