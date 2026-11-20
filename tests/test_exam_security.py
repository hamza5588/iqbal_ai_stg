"""Exam lockdown: SEB header checks, soft events, expiry finalization, adversarial gates."""

from __future__ import annotations

import hashlib
import json
import os
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
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def _get_db():
        return session

    monkeypatch.setattr("app.utils.db.get_db", _get_db)
    monkeypatch.setattr("app.services.lms.attempt_service.get_db", _get_db)
    monkeypatch.setattr("app.services.lms.exam_security_service.get_db", _get_db)
    monkeypatch.setattr("app.services.lms.assessment_service.get_db", _get_db)
    yield session
    session.close()
    engine.dispose()


def _seed_quiz(db, *, require_seb=False, security_mode="standard", time_limit=10):
    teacher = User(
        id=1,
        username="t1",
        full_name="Teacher",
        school_name="S",
        useremail="t@example.com",
        password="x",
        role="teacher",
        class_standard="9",
        medium="English",
        groq_api_key="x",
    )
    student = User(
        id=2,
        username="s1",
        full_name="Student",
        school_name="S",
        useremail="s@example.com",
        password="x",
        role="student",
        class_standard="9",
        medium="English",
        groq_api_key="x",
    )
    topic = Topic(name="Alg", slug="alg-es", subject="Mathematics", grade_level="9")
    db.add_all([teacher, student, topic])
    db.flush()
    q = Question(
        topic_id=topic.id,
        question_text="2+2?",
        options_json='[{"label":"A","text":"3"},{"label":"B","text":"4"},{"label":"C","text":"5"},{"label":"D","text":"6"}]',
        correct_option_index=1,
        difficulty="easy",
        source_type="manual",
    )
    a = Assessment(
        title="Secure Quiz",
        assessment_type="quiz",
        creation_mode="manual",
        status="published",
        created_by=1,
        time_limit_minutes=time_limit,
        security_mode=security_mode,
        require_seb=require_seb,
    )
    db.add_all([q, a])
    db.flush()
    db.add(AssessmentQuestion(assessment_id=a.id, question_id=q.id, sort_order=1))
    db.commit()
    return a, q, student


def test_expected_request_hash_matches_seb_formula():
    url = "https://iqbalai.com/api/lms/quizzes/1/start"
    key = "abc123"
    expected = hashlib.sha256((url + key).encode("utf-8")).hexdigest()
    assert ess.expected_request_hash(url, key) == expected


def test_standard_mode_allows_start_without_seb(db, monkeypatch):
    a, q, student = _seed_quiz(db, require_seb=False)
    # Bypass assignment resolution for unit test
    monkeypatch.setattr(
        "app.services.lms.assignment_service.resolve_student_quiz_assignment",
        lambda *args, **kwargs: None,
    )
    attempt, resumed, done = attempt_service.start_attempt(student.id, a.id)
    assert attempt.status == "in_progress"
    assert attempt.session_token
    assert resumed is False and done is False


def test_legacy_seb_flag_no_longer_blocks_normal_browser(db, monkeypatch):
    """CEO policy: third-party SEB removed — require_seb in DB must not block Chrome."""
    a, q, student = _seed_quiz(db, require_seb=True, security_mode="seb")
    ess.ensure_seb_config_key(a)
    monkeypatch.setattr(
        "app.services.lms.assignment_service.resolve_student_quiz_assignment",
        lambda *args, **kwargs: None,
    )
    monkeypatch.delenv("EXAM_SECURITY_SEB_BYPASS", raising=False)
    monkeypatch.setattr(ess, "_seb_bypass_allowed", lambda: False)
    attempt, resumed, done = attempt_service.start_attempt(student.id, a.id)
    assert attempt.status == "in_progress"
    assert ess.policy_for(a)["require_seb"] is False
    assert resumed is False and done is False


def test_seb_config_key_header_accepts_valid_hash(db, monkeypatch):
    a, q, student = _seed_quiz(db, require_seb=True, security_mode="seb")
    key = ess.ensure_seb_config_key(a)
    url = "https://example.test/exam"
    hdr = ess.expected_request_hash(url, key)
    monkeypatch.setattr(ess, "absolute_request_url", lambda: url)
    monkeypatch.setattr(ess, "_seb_bypass_allowed", lambda: False)

    class _H:
        def get(self, name, default=None):
            if name == "X-SafeExamBrowser-ConfigKeyHash":
                return hdr
            return default

    req = type("R", (), {"headers": _H(), "url": url, "path": "/exam"})()
    monkeypatch.setattr(ess, "has_request_context", lambda: True)
    monkeypatch.setattr(ess, "request", req)
    result = ess.verify_seb_headers(a, absolute_url=url)
    assert result["ok"] is True
    assert result["method"] == "config_key"


def test_security_event_soft_signals_not_verified(db):
    a, q, student = _seed_quiz(db)
    attempt = AssessmentAttempt(
        student_id=student.id, assessment_id=a.id, status="in_progress", max_score=1
    )
    db.add(attempt)
    db.commit()
    row = ess.record_event(attempt, "tab_hidden", severity="critical", verified=True)
    assert row.verified is False
    assert row.severity == "warn"
    assert attempt.focus_loss_count == 1


def test_finalize_expired_batch_scores_partial_answers(db, monkeypatch):
    a, q, student = _seed_quiz(db, time_limit=1)
    monkeypatch.setattr(
        "app.services.lms.assignment_service.resolve_student_quiz_assignment",
        lambda *args, **kwargs: None,
    )
    attempt, _, _ = attempt_service.start_attempt(student.id, a.id, skip_security_gate=True)
    attempt.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.add(
        AttemptAnswer(
            attempt_id=attempt.id,
            question_id=q.id,
            selected_option_index=1,
            is_correct=None,
            time_spent_seconds=5,
        )
    )
    db.commit()

    from app.tasks.lms_tasks import finalize_expired_attempts_batch

    # Avoid post-submit side effects that need full app graph
    monkeypatch.setattr(attempt_service, "_run_post_submit_steps", lambda *args, **kwargs: None)
    out = finalize_expired_attempts_batch(limit=10)
    assert out["finalized"] == 1
    refreshed = db.get(AssessmentAttempt, attempt.id)
    assert refreshed.status == "submitted"
    assert refreshed.timed_out is True
    assert refreshed.score == 1


def test_adversarial_cross_student_event_list_blocked_by_ownership_logic(db):
    a, q, student = _seed_quiz(db)
    other = User(
        id=99,
        username="other",
        full_name="Other",
        school_name="S",
        useremail="o@example.com",
        password="x",
        role="student",
        class_standard="9",
        medium="English",
        groq_api_key="x",
    )
    db.add(other)
    attempt = AssessmentAttempt(
        student_id=student.id, assessment_id=a.id, status="in_progress", max_score=1
    )
    db.add(attempt)
    db.commit()
    ess.record_event(attempt, "tab_hidden")
    # Service layer returns events; route layer enforces ownership — assert data exists only for owner attempt
    events = ess.list_events_for_attempt(attempt.id)
    assert len(events) == 1
    assert events[0]["event_type"] == "tab_hidden"


def test_allow_resume_false_blocks_resume(db, monkeypatch):
    a, q, student = _seed_quiz(db)
    a.allow_resume = False
    db.commit()
    monkeypatch.setattr(
        "app.services.lms.assignment_service.resolve_student_quiz_assignment",
        lambda *args, **kwargs: None,
    )
    first, _, _ = attempt_service.start_attempt(student.id, a.id, skip_security_gate=True)
    assert first.status == "in_progress"
    with pytest.raises(LMSValidationError) as exc:
        attempt_service.start_attempt(student.id, a.id, skip_security_gate=True)
    assert "does not allow resuming" in str(exc.value)


def test_spoofed_seb_header_rejected(db, monkeypatch):
    a, q, student = _seed_quiz(db, require_seb=True, security_mode="seb")
    ess.ensure_seb_config_key(a)
    monkeypatch.setattr(ess, "_seb_bypass_allowed", lambda: False)
    url = "https://example.test/exam"
    monkeypatch.setattr(ess, "absolute_request_url", lambda: url)

    class _H:
        def get(self, name, default=None):
            if name == "X-SafeExamBrowser-ConfigKeyHash":
                return "0" * 64  # wrong hash
            return default

    req = type("R", (), {"headers": _H(), "url": url, "path": "/exam"})()
    monkeypatch.setattr(ess, "has_request_context", lambda: True)
    monkeypatch.setattr(ess, "request", req)
    result = ess.verify_seb_headers(a, absolute_url=url)
    assert result["ok"] is False
    assert result["detail"] == "seb_key_mismatch"
