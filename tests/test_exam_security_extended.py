"""Extended exam-security coverage: windows, settings, session conflicts, API-style attacks."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.database_models import Base, User
from app.models.lms_models import Assessment, AssessmentAttempt, AssessmentQuestion, Question, Topic
from app.services.lms import attempt_service, exam_security_service as ess
from app.services.lms.exceptions import LMSValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


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


def _seed(db, **kwargs):
    teacher = User(
        id=1, username="t1", full_name="T", school_name="S", useremail="t@ex.com",
        password="x", role="teacher", class_standard="9", medium="English", groq_api_key="x",
    )
    student = User(
        id=2, username="s1", full_name="S", school_name="S", useremail="s@ex.com",
        password="x", role="student", class_standard="9", medium="English", groq_api_key="x",
    )
    topic = Topic(name="Alg", slug="alg-ext", subject="Mathematics", grade_level="9")
    db.add_all([teacher, student, topic])
    db.flush()
    q = Question(
        topic_id=topic.id, question_text="1+1?",
        options_json='[{"label":"A","text":"1"},{"label":"B","text":"2"}]',
        correct_option_index=1, difficulty="easy", source_type="manual",
    )
    a = Assessment(
        title="Ext", assessment_type="quiz", creation_mode="manual", status="published",
        created_by=1, time_limit_minutes=10,
        security_mode=kwargs.get("security_mode", "standard"),
        require_seb=kwargs.get("require_seb", False),
    )
    db.add_all([q, a])
    db.flush()
    db.add(AssessmentQuestion(assessment_id=a.id, question_id=q.id, sort_order=1))
    db.commit()
    return a, q, student


def test_update_security_mode_a_b_c(db):
    a, _, _ = _seed(db)
    ess.update_security_settings(a, {"security_mode": "seb"}, actor_id=1)
    assert a.security_mode == "seb" and a.require_seb is True and a.seb_config_key_hash
    key1 = a.seb_config_key_hash
    ess.update_security_settings(a, {"security_mode": "supervised", "max_interruptions": 3}, actor_id=1)
    assert a.security_mode == "supervised" and a.max_interruptions == 3
    ess.update_security_settings(a, {"rotate_seb_config_key": True}, actor_id=1)
    assert a.seb_config_key_hash and a.seb_config_key_hash != key1
    ess.update_security_settings(a, {"security_mode": "standard"}, actor_id=1)
    assert a.security_mode == "standard" and a.require_seb is False


def test_exam_window_blocks_early_and_late(db):
    a, _, _ = _seed(db)
    now = datetime.utcnow()
    a.exam_window_start = now + timedelta(hours=1)
    a.exam_window_end = now + timedelta(hours=2)
    db.commit()
    with pytest.raises(LMSValidationError):
        ess.assert_exam_window(a, now=now)
    a.exam_window_start = now - timedelta(hours=2)
    a.exam_window_end = now - timedelta(hours=1)
    db.commit()
    with pytest.raises(LMSValidationError):
        ess.assert_exam_window(a, now=now)
    a.exam_window_end = now + timedelta(hours=1)
    db.commit()
    ess.assert_exam_window(a, now=now)  # in window


def test_session_token_mismatch_verified_event(db, monkeypatch):
    a, _, student = _seed(db)
    monkeypatch.setattr(
        "app.services.lms.assignment_service.resolve_student_quiz_assignment",
        lambda *args, **kwargs: None,
    )
    attempt, _, _ = attempt_service.start_attempt(student.id, a.id, skip_security_gate=True)
    with pytest.raises(LMSValidationError) as exc:
        ess.require_active_session(attempt, a, session_token="wrong-token")  # noqa: intentional mismatch
    assert "session conflict" in str(exc.value).lower()
    events = ess.list_events_for_attempt(attempt.id)
    assert any(e["event_type"] == "session_conflict" and e["verified"] for e in events)


def test_spoofed_bek_rejected(db, monkeypatch):
    a, _, _ = _seed(db, require_seb=True, security_mode="seb")
    ess.ensure_seb_config_key(a)
    a.seb_browser_exam_keys_json = '["deadbeef"]'
    db.commit()
    url = "https://example.test/exam"
    monkeypatch.setattr(ess, "_seb_bypass_allowed", lambda: False)
    monkeypatch.setattr(ess, "absolute_request_url", lambda: url)

    class _H:
        def get(self, name, default=None):
            if name == "X-SafeExamBrowser-RequestHash":
                return "f" * 64
            return default

    monkeypatch.setattr(ess, "has_request_context", lambda: True)
    monkeypatch.setattr(ess, "request", type("R", (), {"headers": _H(), "url": url, "path": "/exam"})())
    result = ess.verify_seb_headers(a, absolute_url=url)
    assert result["ok"] is False


def test_serialize_policy_hides_secrets(db):
    a, _, _ = _seed(db, require_seb=True, security_mode="seb")
    ess.ensure_seb_config_key(a)
    a.seb_browser_exam_keys_json = '["secretkey"]'
    db.commit()
    ser = ess.serialize_policy(a)
    assert "seb_config_key_hash" not in ser or ser.get("seb_configured") is True
    blob = str(ser)
    assert "secretkey" not in blob
    assert a.seb_config_key_hash not in blob


def test_build_seb_config_contains_start_url(db):
    a, _, _ = _seed(db, require_seb=True, security_mode="seb")
    ess.ensure_seb_config_key(a)
    cfg = ess.build_seb_config_dict(a, "https://iqbalai.test/student/exam/1")
    assert cfg["startURL"].startswith("https://")
    assert cfg["iqbalaiConfigKeyHint"] == a.seb_config_key_hash
