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


def test_tab_hidden_locks_strict_attempt(db, monkeypatch):
    a, qs, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    attempt.current_question_index = 1
    db.commit()
    row = ess.record_event(attempt, "tab_hidden", severity="warn", message="left tab")
    db.refresh(attempt)
    assert attempt.status == "locked"
    assert attempt.lock_reason == "tab_hidden"
    assert attempt.locked_at is not None
    assert getattr(row, "_strict_locked", False) is True
    # Answers rejected while locked
    out = attempt_service.save_answer(attempt.id, qs[0].id, selected_option_index=1)
    assert out["saved"] is False and out["reason"] == "exam_locked"


def test_standard_mode_does_not_lock(db, monkeypatch):
    a, _, student, _, _ = _seed(db, security_mode="standard")
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "tab_hidden", severity="warn")
    db.refresh(attempt)
    assert attempt.status == "in_progress"


def test_duplicate_lock_idempotent(db, monkeypatch):
    a, _, student, _, _ = _seed(db)
    attempt = _start(db, a, student, monkeypatch)
    ess.record_event(attempt, "tab_hidden", severity="warn")
    db.refresh(attempt)
    locked_at = attempt.locked_at
    ess.record_event(attempt, "window_blur", severity="warn")
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
    ess.record_event(attempt, "fullscreen_exit", severity="warn")
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
    ess.record_event(attempt, "tab_hidden", severity="warn")
    assert ess.teacher_may_manage_attempt(outsider.id, attempt, role="teacher") is False
    assert ess.teacher_may_manage_attempt(1, attempt, role="teacher") is True


def test_timer_continues_during_lock(db, monkeypatch):
    a, _, student, teacher, _ = _seed(db, time_limit=30)
    attempt = _start(db, a, student, monkeypatch)
    deadline = datetime.utcnow() + timedelta(minutes=20)
    attempt.expires_at = deadline
    db.commit()
    ess.record_event(attempt, "tab_hidden", severity="warn")
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
    ess.record_event(attempt, "tab_hidden", severity="warn")
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
    ess.record_event(attempt, "tab_hidden", severity="warn")
    again, resumed, done = attempt_service.start_attempt(student.id, a.id)
    assert resumed and not done
    assert again.id == attempt.id
    assert again.status == "locked"


def test_update_security_accepts_strict(db):
    a, _, _, _, _ = _seed(db, security_mode="standard")
    ess.update_security_settings(a, {"security_mode": "strict"}, actor_id=1)
    assert a.security_mode == "strict"
    assert ess.policy_for(a)["lock_on_leave"] is True
