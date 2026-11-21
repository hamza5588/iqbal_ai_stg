#!/usr/bin/env python3
"""Human-like exploratory checks for Strict Exam lock leaks (API + client mirror).

Does not require Playwright. Exercises refresh/resume, re-entry, double start,
timer expiry, rapid leave events, teacher unlock then re-lock, and client
handleServerLock races that previously auto-unlocked.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EVIDENCE = ROOT / "docs" / "qa_evidence"
EVIDENCE.mkdir(parents=True, exist_ok=True)


def _client_handle_server_lock(state: dict, data: dict) -> dict:
    """Mirror of lms-exam-security.js handleServerLock after strict3 fix."""
    if not data:
        return state
    if data.get("session_token"):
        state["sessionToken"] = data["session_token"]
    locked = data.get("is_locked") or data.get("status") == "locked" or data.get("locked")
    if locked:
        state["requiresTeacherUnlock"] = True
        if not state.get("lockUiShown"):
            state["lockUiShown"] = True
            state["locked"] = True
    elif state.get("lockUiShown") or state.get("requiresTeacherUnlock"):
        teacher_unlock = data.get("unlocked") is True or data.get("exam_unlocked") is True
        if teacher_unlock and (
            data.get("status") == "in_progress" or data.get("is_locked") is False
        ):
            state["requiresTeacherUnlock"] = False
            state["lockUiShown"] = False
            state["locked"] = False
    return state


def main() -> int:
    db_path = Path(tempfile.gettempdir()) / f"strict_edge_{uuid.uuid4().hex}.db"
    os.environ["DATABASE_URL"] = "sqlite:///" + str(db_path).replace("\\", "/")
    os.environ["SKIP_EXTRA_STARTUP"] = "true"
    os.environ["FLASK_ENV"] = "development"
    os.environ["ENV"] = "local"
    os.environ.setdefault("SECRET_KEY", "strict-edge-secret")
    for k in ("POSTGRES_HOST", "MYSQL_HOST"):
        os.environ.pop(k, None)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models.database_models import Base, User
    from app.models.lms_models import (
        Assessment,
        AssessmentQuestion,
        Question,
        Topic,
    )
    from app.services.lms import attempt_service, exam_security_service as ess
    from app.services.lms.exceptions import LMSValidationError

    uri = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(uri, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def _get_db():
        return session

    # Patch get_db like unit tests
    import app.utils.db as dbmod
    import app.services.lms.attempt_service as asvc_mod
    import app.services.lms.exam_security_service as ess_mod
    import app.services.lms.assessment_service as asses_mod

    dbmod.get_db = _get_db
    asvc_mod.get_db = _get_db
    ess_mod.get_db = _get_db
    asses_mod.get_db = _get_db

    teacher = User(
        username="edge_t",
        full_name="Edge Teacher",
        school_name="E",
        useremail="edge.t@test.local",
        password="x",
        role="teacher",
        class_standard="9",
        medium="English",
        groq_api_key="x",
        is_active=True,
    )
    student = User(
        username="edge_s",
        full_name="Edge Student",
        school_name="E",
        useremail="edge.s@test.local",
        password="x",
        role="student",
        class_standard="9",
        medium="English",
        groq_api_key="x",
        is_active=True,
    )
    topic = Topic(
        name="Edge",
        slug=f"edge-{uuid.uuid4().hex[:8]}",
        subject="Mathematics",
        grade_level="9",
    )
    session.add_all([teacher, student, topic])
    session.flush()
    q = Question(
        topic_id=topic.id,
        question_text="1+1?",
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
        title="Strict Edge Quiz",
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
    session.add_all([q, a])
    session.flush()
    session.add(AssessmentQuestion(assessment_id=a.id, question_id=q.id, sort_order=1))
    session.commit()

    import app.services.lms.assignment_service as assign_mod

    assign_mod.resolve_student_quiz_assignment = lambda *args, **kwargs: None

    results = []
    case_n = {"i": 0}

    def check(name: str, fn):
        try:
            ok, detail = fn()
            results.append({"case": name, "result": "locked_correctly" if ok else "leaked", "detail": detail, "pass": ok})
            print(("PASS" if ok else "LEAK"), name, "-", detail)
        except Exception as exc:  # noqa: BLE001
            results.append({"case": name, "result": "error", "detail": str(exc), "pass": False})
            print("ERROR", name, "-", exc)

    def _new_quiz():
        """Isolated quiz per case so submitted/abandoned state cannot pollute later cases."""
        case_n["i"] += 1
        qq = Question(
            topic_id=topic.id,
            question_text=f"Edge Q{case_n['i']}?",
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
        aa = Assessment(
            title=f"Strict Edge Quiz {case_n['i']}",
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
        session.add_all([qq, aa])
        session.flush()
        session.add(AssessmentQuestion(assessment_id=aa.id, question_id=qq.id, sort_order=1))
        session.commit()
        return aa, qq

    def _fresh_locked():
        quiz, question = _new_quiz()
        attempt, _, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        ess.record_event(attempt, "fullscreen_exit", severity="warn")
        session.refresh(attempt)
        assert attempt.status == "locked", attempt.status
        return attempt, quiz, question

    # 1) refresh mid-lock → resume
    def case_refresh_mid_lock():
        attempt, quiz, _ = _fresh_locked()
        again, resumed, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        payload = ess.attempt_lock_payload(again)
        state = {"locked": True, "lockUiShown": True, "requiresTeacherUnlock": True, "sessionToken": attempt.session_token}
        state = _client_handle_server_lock(state, {**payload, "session_token": again.session_token})
        state = _client_handle_server_lock(
            state, {"status": "in_progress", "is_locked": False, "session_token": again.session_token}
        )
        ok = again.status == "locked" and payload["is_locked"] and state["locked"]
        return ok, f"server={again.status} client_locked={state['locked']} resumed={resumed}"

    check("refresh mid-lock → resume", case_refresh_mid_lock)

    # 2) close tab / reopen (new start call, no client state)
    def case_close_reopen():
        attempt, quiz, _ = _fresh_locked()
        again, resumed, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        state = {"locked": False, "lockUiShown": False, "requiresTeacherUnlock": False, "sessionToken": None}
        d = ess.attempt_lock_payload(again)
        d["session_token"] = again.session_token
        if d.get("is_locked") or d.get("status") == "locked":
            state["locked"] = True
            state["lockUiShown"] = True
            state["requiresTeacherUnlock"] = True
            state["sessionToken"] = again.session_token
        ok = again.id == attempt.id and again.status == "locked" and state["locked"]
        return ok, f"same_attempt={again.id == attempt.id} status={again.status} client={state['locked']}"

    check("close tab → reopen → resume", case_close_reopen)

    # 3) resume after lock (explicit)
    def case_resume_after_lock():
        attempt, quiz, _ = _fresh_locked()
        again, resumed, done = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        ok = resumed and not done and again.status == "locked"
        return ok, f"resumed={resumed} status={again.status}"

    check("resume after lock", case_resume_after_lock)

    # 4) double-refresh
    def case_double_refresh():
        attempt, quiz, _ = _fresh_locked()
        a1, _, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        a2, _, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        ok = a1.status == "locked" and a2.status == "locked" and a1.id == a2.id == attempt.id
        return ok, f"ids={attempt.id},{a1.id},{a2.id} statuses={a1.status},{a2.status}"

    check("double-refresh resume", case_double_refresh)

    # 5) switching networks (offline gap then heartbeat)
    def case_network_switch():
        attempt, quiz, _ = _fresh_locked()
        time.sleep(0.05)
        hb = attempt_service.heartbeat(attempt.id, session_token=attempt.session_token)
        state = {"locked": True, "lockUiShown": True, "requiresTeacherUnlock": True, "sessionToken": attempt.session_token}
        state = _client_handle_server_lock(state, hb)
        state = _client_handle_server_lock(
            state, {"status": "in_progress", "is_locked": False, "session_token": attempt.session_token}
        )
        ok = hb.get("status") == "locked" and state["locked"]
        return ok, f"hb_status={hb.get('status')} client={state['locked']}"

    check("switching networks / stale cache heartbeat", case_network_switch)

    # 6) slow load (questions fetch while locked)
    def case_slow_load_questions():
        attempt, quiz, _ = _fresh_locked()
        state = {"locked": True, "lockUiShown": True, "requiresTeacherUnlock": True, "sessionToken": attempt.session_token}
        delivery = attempt_service.get_attempt_delivery_state(attempt.id)
        state = _client_handle_server_lock(
            state, {"status": "in_progress", "is_locked": False, "session_token": attempt.session_token}
        )
        session.refresh(attempt)
        ok = (
            attempt.status == "locked"
            and state["locked"]
            and delivery.get("status") == "locked"
            and delivery.get("is_locked") is True
        )
        return ok, f"status={attempt.status} client={state['locked']} delivery_locked={delivery.get('is_locked')} q={len(delivery.get('questions') or [])}"

    check("slow load / questions while locked", case_slow_load_questions)

    # 7) starting "new" attempt while locked → must resume same locked
    def case_new_attempt_while_locked():
        attempt, quiz, _ = _fresh_locked()
        again, resumed, done = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        ok = resumed and again.id == attempt.id and again.status == "locked" and not done
        return ok, f"resumed={resumed} same={again.id == attempt.id} status={again.status}"

    check("start new attempt while locked", case_new_attempt_while_locked)

    # 8) teacher unlock then re-lock
    def case_unlock_then_relock():
        attempt, quiz, _ = _fresh_locked()
        ess.unlock_attempt(attempt, teacher_id=teacher.id, message="temp unlock")
        session.refresh(attempt)
        if attempt.status != "in_progress":
            return False, f"unlock failed status={attempt.status}"
        ess.record_event(attempt, "tab_hidden", severity="warn")
        session.refresh(attempt)
        again, _, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        state = {"locked": False, "lockUiShown": False, "requiresTeacherUnlock": False}
        if again.status == "locked":
            state["locked"] = True
            state["lockUiShown"] = True
            state["requiresTeacherUnlock"] = True
        state = _client_handle_server_lock(
            state, {"status": "in_progress", "is_locked": False, "session_token": again.session_token}
        )
        ok = again.status == "locked" and state["locked"]
        return ok, f"after_relock={again.status} client={state['locked']}"

    check("teacher unlock then re-lock", case_unlock_then_relock)

    # 9) lock during question fetch (race: questions then lock event)
    def case_lock_during_question_fetch():
        quiz, question = _new_quiz()
        attempt, _, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        delivery = attempt_service.get_attempt_delivery_state(attempt.id)
        ess.record_event(attempt, "window_blur", severity="warn")
        session.refresh(attempt)
        state = {"locked": False, "lockUiShown": False, "requiresTeacherUnlock": False, "sessionToken": attempt.session_token}
        state = _client_handle_server_lock(state, {**ess.attempt_lock_payload(attempt), "session_token": attempt.session_token})
        ans = attempt_service.save_answer(attempt.id, question.id, selected_option_index=1, time_spent_seconds=1)
        ok = (
            attempt.status == "locked"
            and state["locked"]
            and ans.get("saved") is False
            and ans.get("reason") == "exam_locked"
            and isinstance(delivery.get("questions"), list)
        )
        return ok, f"status={attempt.status} save={ans.get('reason')} client={state['locked']} qs={len(delivery.get('questions') or [])}"

    check("lock during question fetch", case_lock_during_question_fetch)

    # 10) lock right as timer expires
    def case_lock_as_timer_expires():
        attempt, quiz, _ = _fresh_locked()
        attempt.expires_at = datetime.utcnow() - timedelta(seconds=2)
        session.commit()
        attempt_service.finalize_expired_attempt(attempt.id)
        session.refresh(attempt)
        ok = attempt.status == "submitted"
        try:
            again, resumed, done = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
            leaked = again.id == attempt.id and again.status == "in_progress"
            ok = ok and not leaked
            detail = f"final={attempt.status} next_id={again.id} next_status={again.status} resumed={resumed} done={done}"
        except LMSValidationError as e:
            detail = f"final={attempt.status} start_error={e}"
        return ok, detail

    check("lock right as timer expires", case_lock_as_timer_expires)

    # 11) multiple rapid fullscreen exits
    def case_rapid_fullscreen_exits():
        quiz, _ = _new_quiz()
        attempt, _, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        for _ in range(5):
            ess.record_event(attempt, "fullscreen_exit", severity="warn")
            session.refresh(attempt)
        hb = attempt_service.heartbeat(attempt.id, session_token=attempt.session_token)
        state = {"locked": True, "lockUiShown": True, "requiresTeacherUnlock": True, "sessionToken": attempt.session_token}
        state = _client_handle_server_lock(state, hb)
        ok = attempt.status == "locked" and hb.get("status") == "locked" and state["locked"]
        return ok, f"status={attempt.status} hb={hb.get('status')} client={state['locked']}"

    check("multiple rapid fullscreen exits", case_rapid_fullscreen_exits)

    # 12) browser back button (resume event while locked + stale in_progress)
    def case_browser_back():
        attempt, quiz, _ = _fresh_locked()
        again, resumed, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        state = {
            "locked": True,
            "lockUiShown": True,
            "requiresTeacherUnlock": True,
            "sessionToken": again.session_token,
        }
        state = _client_handle_server_lock(
            state,
            {
                "status": "in_progress",
                "is_locked": False,
                "resumed": True,
                "session_token": again.session_token,
            },
        )
        ok = again.status == "locked" and state["locked"]
        return ok, f"server={again.status} client={state['locked']} resumed={resumed}"

    check("browser back button / cached resume", case_browser_back)

    # 13) clearing site data partially (lost sessionToken, fresh start)
    def case_partial_site_data_clear():
        attempt, quiz, _ = _fresh_locked()
        again, resumed, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        state = {"locked": False, "lockUiShown": False, "requiresTeacherUnlock": False, "sessionToken": None}
        payload = ess.attempt_lock_payload(again)
        if payload.get("is_locked") or payload.get("status") == "locked":
            state["locked"] = True
            state["lockUiShown"] = True
            state["requiresTeacherUnlock"] = True
            state["sessionToken"] = again.session_token
        hb = attempt_service.heartbeat(again.id, session_token=again.session_token)
        state = _client_handle_server_lock(state, hb)
        ok = again.status == "locked" and state["locked"] and hb.get("is_locked") is True
        return ok, f"status={again.status} client={state['locked']} hb_locked={hb.get('is_locked')}"

    check("clearing site data partially", case_partial_site_data_clear)

    # 14) teacher unlock then resume → must unlock
    def case_teacher_unlock_resume_ok():
        attempt, quiz, _ = _fresh_locked()
        unlocked = ess.unlock_attempt(attempt, teacher_id=teacher.id, message="go")
        again, resumed, _ = attempt_service.start_attempt(student.id, quiz.id, skip_security_gate=True)
        state = {"locked": True, "lockUiShown": True, "requiresTeacherUnlock": True, "sessionToken": "old"}
        hb = attempt_service.heartbeat(again.id, session_token="old")
        state = _client_handle_server_lock(state, hb)
        ok = (
            unlocked.status == "in_progress"
            and again.status == "in_progress"
            and state["locked"] is False
            and hb.get("unlocked") is True
        )
        return ok, f"status={again.status} client={state['locked']} unlocked_flag={hb.get('unlocked')}"

    check("teacher unlock → resume (must unlock)", case_teacher_unlock_resume_ok)

    # 15) token rotation alone must NOT unlock
    def case_token_rotation_no_unlock():
        state = {
            "locked": True,
            "lockUiShown": True,
            "requiresTeacherUnlock": True,
            "sessionToken": "tok-a",
        }
        state = _client_handle_server_lock(
            state,
            {"status": "in_progress", "is_locked": False, "session_token": "tok-b"},
        )
        ok = state["locked"] is True
        return ok, f"client_locked={state['locked']}"

    check("token rotation alone (must stay locked)", case_token_rotation_no_unlock)

    passed = sum(1 for r in results if r["pass"])
    failed = [r for r in results if not r["pass"]]
    report = {
        "passed": passed,
        "total": len(results),
        "all_pass": passed == len(results),
        "results": results,
        "failed": failed,
    }
    out = EVIDENCE / "strict_lock_edge_cases.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": passed, "total": len(results), "all_pass": report["all_pass"]}, indent=2))
    session.close()
    engine.dispose()
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
