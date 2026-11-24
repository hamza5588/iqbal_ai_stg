"""Strict Exam Mode: lock on leave, teacher unlock/extend, timer continues."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database_models import Base, User
from app.models.lms_models import (
    Assessment,
    AssessmentAttempt,
    AssessmentQuestion,
    AttemptAnswer,
    ExamSecurityEvent,
    Question,
    Topic,
)
from app.services.lms import attempt_service, exam_security_service as ess
from app.services.lms.exceptions import LMSValidationError


@pytest.fixture()
def db(monkeypatch):
    import uuid

    from sqlalchemy.pool import StaticPool

    # Unique shared-memory URI avoids cross-test :memory: StaticPool collisions.
    uri = f"sqlite:///file:strict_{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
    engine = create_engine(
        uri,
        connect_args={"check_same_thread": False, "uri": True},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def _get_db():
        return session

    monkeypatch.setattr("app.utils.db.get_db", _get_db)
    monkeypatch.setattr("app.services.lms.attempt_service.get_db", _get_db)
    monkeypatch.setattr("app.services.lms.exam_security_service.get_db", _get_db)
    monkeypatch.setattr("app.services.lms.assessment_service.get_db", _get_db)
    monkeypatch.setattr("app.services.lms.performance_service.get_db", _get_db)
    yield session
    session.close()
    engine.dispose()
    try:
        from app.utils.db import reset_db_engine

        reset_db_engine()
    except Exception:
        pass


def _seed(db, *, security_mode="strict", time_limit=30):
    teacher = User(
        id=1, username="t1", full_name="Teacher", school_name="S",
        useremail="t@example.com", password="x", role="teacher",
        class_standard="9", medium="English", groq_api_key="x",
    )
    student = User(
        id=2, username="s1", full_name="Student One", school_name="S",
        useremail="s@example.com", password="x", role="student",
        class_standard="9", medium="English", groq_api_key="x",
    )
    outsider = User(
        id=3, username="t2", full_name="Other Teacher", school_name="S",
        useremail="t2@example.com", password="x", role="teacher",
        class_standard="9", medium="English", groq_api_key="x",
    )
    topic = Topic(name="Alg", slug="alg-strict", subject="Mathematics", grade_level="9")
    db.add_all([teacher, student, outsider, topic])
    db.flush()
    qs = []
    for i in range(3):
        q = Question(
            topic_id=topic.id,
            question_text=f"Q{i}?",
            options_json='[{"label":"A","text":"1"},{"label":"B","text":"2"},{"label":"C","text":"3"},{"label":"D","text":"4"}]',
            correct_option_index=1,
            difficulty="easy",
            source_type="manual",
        )
        qs.append(q)
    a = Assessment(
        title="Strict Quiz",
        assessment_type="quiz",
        creation_mode="manual",
        status="published",
        created_by=1,
        time_limit_minutes=time_limit,
        security_mode=security_mode,
    )
    db.add_all(qs + [a])
    db.flush()
    for i, q in enumerate(qs):
        db.add(AssessmentQuestion(assessment_id=a.id, question_id=q.id, sort_order=i + 1))
    db.commit()
    return a, qs, student, teacher, outsider


def _start(db, a, student, monkeypatch):
    monkeypatch.setattr(
        "app.services.lms.assignment_service.resolve_student_quiz_assignment",
        lambda *args, **kwargs: None,
    )
    attempt, resumed, done = attempt_service.start_attempt(student.id, a.id)
    assert attempt.status == "in_progress" and not done
    return attempt


def test_policy_strict_flags(db):
    a, _, _, _, _ = _seed(db)
    p = ess.policy_for(a)
    assert p["is_strict"] and p["lock_on_leave"] and p["require_fullscreen"]


def test_browser_closed_locks_strict_attempt(db, monkeypatch):
    a, qs, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    attempt.current_question_index = 1
    db.commit()
    row = ess.record_event(attempt, "browser_closed", severity="warn", message="left tab")
    db.refresh(attempt)
    assert attempt.status == "locked"
    assert attempt.lock_reason == "browser_closed"
    assert attempt.locked_at is not None
    assert getattr(row, "_strict_locked", False) is True
    # Answers rejected while locked
    out = attempt_service.save_answer(attempt.id, qs[0].id, selected_option_index=1)
    assert out["saved"] is False and out["reason"] == "exam_locked"


def test_standard_mode_does_not_lock(db, monkeypatch):
    a, _, student, _, _ = _seed(db, security_mode="standard")
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "in_progress"


def test_duplicate_lock_idempotent(db, monkeypatch):
    a, _, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    locked_at = attempt.locked_at
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "locked"
    assert attempt.locked_at == locked_at
    lock_events = (
        db.query(ExamSecurityEvent)
        .filter(ExamSecurityEvent.attempt_id == attempt.id, ExamSecurityEvent.event_type == "exam_locked")
        .count()
    )
    assert lock_events == 1


def test_teacher_unlock_resumes_same_attempt_and_answers(db, monkeypatch):
    a, qs, student, teacher, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    attempt_service.save_answer(attempt.id, qs[0].id, selected_option_index=1)
    attempt.current_question_index = 2
    db.commit()
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "locked"
    aid = attempt.id
    updated = ess.unlock_attempt(attempt, teacher_id=teacher.id)
    assert updated.id == aid
    assert updated.status == "in_progress"
    assert updated.current_question_index == 2
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == aid).all()
    assert len(answers) == 1 and answers[0].selected_option_index == 1


def test_unauthorized_unlock_helper(db, monkeypatch):
    a, _, student, _, outsider = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "browser_closed", severity="warn")
    assert ess.teacher_may_manage_attempt(outsider.id, attempt, role="teacher") is False
    assert ess.teacher_may_manage_attempt(1, attempt, role="teacher") is True


def test_timer_continues_during_lock(db, monkeypatch):
    a, _, student, teacher, _ = _seed(db, time_limit=30)
    attempt = _start(db, a, student, monkeypatch)
    deadline = datetime.utcnow() + timedelta(minutes=20)
    attempt.expires_at = deadline
    db.commit()
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "locked"
    assert attempt.expires_at == deadline
    ess.unlock_attempt(attempt, teacher_id=teacher.id)
    db.refresh(attempt)
    assert attempt.expires_at == deadline


def test_teacher_extend_time(db, monkeypatch):
    a, _, student, teacher, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    attempt.expires_at = datetime.utcnow() + timedelta(minutes=10)
    db.commit()
    prev = attempt.expires_at
    r1 = ess.extend_attempt_time(
        attempt, teacher_id=teacher.id, seconds=300, idempotency_key="k1"
    )
    assert r1["extended"] is True
    db.refresh(attempt)
    assert attempt.expires_at == prev + timedelta(seconds=300)
    r2 = ess.extend_attempt_time(
        attempt, teacher_id=teacher.id, seconds=300, idempotency_key="k1"
    )
    assert r2["duplicate"] is True


def test_expire_while_locked(db, monkeypatch):
    a, qs, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    attempt_service.save_answer(attempt.id, qs[0].id, selected_option_index=1)
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "locked"
    attempt.expires_at = datetime.utcnow() - timedelta(seconds=5)
    db.commit()
    monkeypatch.setattr(attempt_service, "_run_post_submit_steps", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.services.lms.performance_service.analyze_attempt",
        lambda *args, **kwargs: {"weak_topics": [], "strong_topics": [], "all_topics": []},
    )
    monkeypatch.setattr(
        "app.services.lms.performance_service.topic_breakdown_for_attempt",
        lambda *args, **kwargs: [],
    )
    result = attempt_service.finalize_expired_attempt(attempt.id)
    db.refresh(attempt)
    assert attempt.status == "submitted"
    assert attempt.timed_out is True
    assert result["score"] >= 0


def test_refresh_retains_locked_state(db, monkeypatch):
    a, _, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "browser_closed", severity="warn")
    again, resumed, done = attempt_service.start_attempt(student.id, a.id)
    assert resumed and not done
    assert again.id == attempt.id
    assert again.status == "locked"


def test_update_security_accepts_strict(db):
    a, _, _, _, _ = _seed(db, security_mode="standard")
    ess.update_security_settings(a, {"security_mode": "strict"}, actor_id=1)
    assert a.security_mode == "strict"
    assert ess.policy_for(a)["lock_on_leave"] is True


def test_second_leave_while_locked_stays_locked(db, monkeypatch):
    """Server must stay locked when student leaves again after the first strict lock."""
    a, _, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "locked"
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "locked"
    hb = attempt_service.heartbeat(attempt.id, session_token=attempt.session_token)
    assert hb["status"] == "locked" and hb["is_locked"] is True


def test_heartbeat_while_locked_never_reports_in_progress(db, monkeypatch):
    a, _, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    for _ in range(3):
        info = attempt_service.heartbeat(attempt.id, session_token=attempt.session_token)
        assert info["status"] == "locked"
        assert info["is_locked"] is True


def test_already_locked_security_event_api_shape(db, monkeypatch):
    """POST /security-events while locked must tell the client the attempt is still locked."""
    a, _, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    payload = {
        "recorded": False,
        "reason": "already_locked",
        "status": "locked",
        **ess.attempt_lock_payload(attempt),
    }
    assert payload["recorded"] is False
    assert payload["status"] == "locked" and payload["is_locked"] is True
    assert attempt.status == "locked"


def _client_handle_server_lock(state: dict, data: dict) -> dict:
    """Mirror of static/lms/lms-exam-security.js handleServerLock (strict unlock gate)."""
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


def test_double_cross_then_continue_payload_sequence_keeps_client_lock(db, monkeypatch):
    """Simulate second leave + server already_locked response + heartbeat while still locked."""
    a, _, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    state = {"locked": True, "lockUiShown": True}
    second = {
        "recorded": False,
        "reason": "already_locked",
        **ess.attempt_lock_payload(attempt),
    }
    hb = attempt_service.heartbeat(attempt.id, session_token=attempt.session_token)
    state = _client_handle_server_lock(state, second)
    state = _client_handle_server_lock(state, hb)
    assert attempt.status == "locked"
    assert state["locked"] and state["lockUiShown"]


def test_double_cross_then_stale_heartbeat_must_keep_client_lock():
    """Regression: strict lock must not clear on a stale in_progress heartbeat/event.

    Reproduces: lock shown locally on leave → heartbeat arrives before/with wrong
    status → student clicks Continue and can take the quiz while server may still be locked.
    """
    state = {
        "locked": False,
        "lockUiShown": False,
        "requiresTeacherUnlock": False,
        "sessionToken": "tok-a",
    }
    # First cross: client fail-closed (lms-exam-security.js showLockedOverlay before POST completes)
    state["locked"] = True
    state["lockUiShown"] = True
    state["requiresTeacherUnlock"] = True
    # Concurrent or follow-up heartbeat while DB already locked — common wrong client response:
    stale = {
        "status": "in_progress",
        "is_locked": False,
        "remaining_seconds": 600,
        "session_token": "tok-a",  # same token → not a teacher unlock
    }
    state = _client_handle_server_lock(state, stale)
    assert state["locked"] is True and state["lockUiShown"] is True, (
        "Client handleServerLock cleared the lock on status=in_progress without teacher unlock"
    )


def test_teacher_unlock_payload_clears_client_lock():
    """Teacher unlock must set unlocked=true — token rotation alone must not unlock."""
    state = {
        "locked": True,
        "lockUiShown": True,
        "requiresTeacherUnlock": True,
        "sessionToken": "tok-a",
    }
    # Token rotation without unlocked flag (stale / resume race) must keep lock.
    rotated_only = {
        "status": "in_progress",
        "is_locked": False,
        "session_token": "tok-b",
    }
    state = _client_handle_server_lock(state, rotated_only)
    assert state["locked"] is True and state["lockUiShown"] is True
    unlocked = {
        "status": "in_progress",
        "is_locked": False,
        "unlocked": True,
        "session_token": "tok-b",
    }
    state = _client_handle_server_lock(state, unlocked)
    assert state["locked"] is False and state["lockUiShown"] is False


def test_double_cross_server_locked_client_keeps_lock_ui():
    """When server payloads stay locked, client logic must keep the lock UI shown."""
    state = {"locked": True, "lockUiShown": True, "requiresTeacherUnlock": True}
    for payload in (
        {"status": "locked", "is_locked": True, "reason": "already_locked"},
        {"status": "locked", "is_locked": True},
    ):
        state = _client_handle_server_lock(state, payload)
    assert state["locked"] and state["lockUiShown"]


def test_refresh_resume_payload_stays_locked(db, monkeypatch):
    """Resume/start after lock must keep status locked and is_locked true (no auto-unlock)."""
    a, _, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    old_token = attempt.session_token
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "locked"
    again, resumed, done = attempt_service.start_attempt(student.id, a.id)
    assert resumed and not done
    assert again.status == "locked"
    assert again.session_token == old_token
    payload = ess.attempt_lock_payload(again)
    assert payload["is_locked"] is True and payload["status"] == "locked"
    # Stale client resume must not clear lock without unlocked flag.
    state = {
        "locked": True,
        "lockUiShown": True,
        "requiresTeacherUnlock": True,
        "sessionToken": old_token,
    }
    state = _client_handle_server_lock(
        state,
        {
            "status": again.status,
            "is_locked": payload["is_locked"],
            "session_token": again.session_token,
            "lock_reason": again.lock_reason,
        },
    )
    assert state["locked"] is True
    # Fake "resume as unlocked" in_progress must still keep client lock.
    state = _client_handle_server_lock(
        state,
        {"status": "in_progress", "is_locked": False, "session_token": old_token},
    )
    assert state["locked"] is True


def test_teacher_unlock_then_resume_clears_lock(db, monkeypatch):
    a, _, student, teacher, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    unlocked = ess.unlock_attempt(attempt, teacher_id=teacher.id, message="ok")
    assert unlocked.status == "in_progress"
    again, resumed, done = attempt_service.start_attempt(student.id, a.id)
    assert resumed and again.status == "in_progress"
    assert ess.attempt_lock_payload(again)["is_locked"] is False
    # Heartbeat with stale pre-unlock token must sync unlocked=true.
    hb = attempt_service.heartbeat(again.id, session_token="stale-pre-unlock-token")
    assert hb.get("unlocked") is True
    assert hb.get("session_token") == again.session_token
    state = {
        "locked": True,
        "lockUiShown": True,
        "requiresTeacherUnlock": True,
        "sessionToken": "stale-pre-unlock-token",
    }
    state = _client_handle_server_lock(state, hb)
    assert state["locked"] is False


def test_side_click_and_tab_switch_never_lock_strict(db, monkeypatch):
    """DIL feedback 24-11-2026: focus loss / tab switch / fullscreen exit only log."""
    a, _, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    for ev in ("window_blur", "tab_hidden", "fullscreen_exit", "focus_loss", "reload"):
        ess.record_event(attempt, ev, severity="warn")
        db.refresh(attempt)
        assert attempt.status == "in_progress", ev
    ess.record_event(attempt, "browser_closed", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "locked" and attempt.lock_reason == "browser_closed"


def test_leaving_quiz_tab_locks_strict(db, monkeypatch):
    """DIL 25-11-2026: going to another tab while the quiz is open locks it."""
    a, _, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    row = ess.record_event(attempt, "left_quiz_tab", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "locked" and attempt.lock_reason == "left_quiz_tab"
    assert getattr(row, "_strict_locked", False) is True
