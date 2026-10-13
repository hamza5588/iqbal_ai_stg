"""Admin removes / replaces a published diagnostic: what each student of that grade sees.

- took the old one  -> old result stays, the new one is offered as a normal first attempt
- took neither      -> both are owed, old first, then the new one
"""
import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.lms_models import Assessment, AssessmentAttempt, AssessmentQuestion, Base, Question
from app.services.lms.exceptions import LMSValidationError

GRADE = "9"


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()

    import app.utils.db as db_mod
    from app.routes import lms_routes
    from app.services.lms import (
        assessment_service,
        attempt_service,
        class_service,
        diagnostic_service,
        diagnostic_timer_service,
        diagnostic_variant_service,
        student_profile_service,
    )

    monkeypatch.setattr(db_mod, "get_session_factory", lambda: (lambda: s))
    monkeypatch.setattr(db_mod, "close_db", lambda e=None: None, raising=False)
    monkeypatch.setattr(class_service, "get_student_grade", lambda sid: GRADE)
    monkeypatch.setattr(class_service, "list_student_classes", lambda sid: [])
    monkeypatch.setattr(assessment_service, "has_target_pdf", lambda aid: True)
    monkeypatch.setattr(diagnostic_timer_service, "estimate_question_times", lambda aid: None)
    monkeypatch.setattr(diagnostic_variant_service, "enqueue_pool_prewarm", lambda aid: None)
    monkeypatch.setattr(
        student_profile_service.assignment_service, "list_assignments_for_student", lambda sid: []
    )

    # Post-submit: keep only the profile update (mastery / learning path / LLM are out of scope).
    def post_submit(attempt, assessment):
        if assessment.assessment_type == "diagnostic":
            student_profile_service.mark_diagnostic_complete(attempt.student_id, assessment_id=assessment.id)

    monkeypatch.setattr(attempt_service, "_run_post_submit_steps", post_submit)
    from app.services.lms import performance_service

    monkeypatch.setattr(performance_service, "topic_breakdown_for_attempt", lambda aid: [])
    monkeypatch.setattr(performance_service, "analyze_attempt", lambda aid: {})
    monkeypatch.setattr("app.utils.auth.sync_session_user", lambda: True)

    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(lms_routes.bp, url_prefix="/api/lms")
    with app.app_context():
        yield {
            "s": s,
            "app": app,
            "assessment_service": assessment_service,
            "attempt_service": attempt_service,
            "diagnostic_service": diagnostic_service,
        }
    s.close()


def _mk_diag(s, title, grade=GRADE, status="draft", n_questions=2):
    a = Assessment(title=title, assessment_type="diagnostic", grade_level=grade, status=status)
    s.add(a)
    s.flush()
    for i in range(n_questions):
        q = Question(question_text=f"{title} q{i}", options_json='["a","b"]', correct_option_index=0)
        s.add(q)
        s.flush()
        s.add(AssessmentQuestion(assessment_id=a.id, question_id=q.id, sort_order=i))
    s.commit()
    return a.id


def _take(env, student_id, assessment_id):
    at = env["attempt_service"]
    attempt, resumed, done = at.start_attempt(student_id, assessment_id)
    assert not done
    at.submit_attempt(attempt.id)
    return attempt.id


def _client(env, student_id):
    c = env["app"].test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = student_id
        sess["role"] = "student"
    return c


def _replace(env, remove_first=True):
    """Publish OLD, then (optionally remove it and) publish NEW for the same grade."""
    s, svc = env["s"], env["assessment_service"]
    old = _mk_diag(s, "old")
    svc.publish_assessment(old)
    return old, s, svc


def test_single_published_diagnostic_is_unchanged(env):
    old, s, svc = _replace(env)
    ds = env["diagnostic_service"]
    assert [a.id for a in ds.get_student_diagnostic_queue(1)] == [old]
    summary = ds.student_diagnostic_queue_summary(1)
    assert summary["pending_count"] == 1 and summary["message"] is None
    data = _client(env, 1).get("/api/lms/diagnostics/default").get_json()["data"]
    assert data["id"] == old and data["attempted_this_diagnostic"] is False
    assert data["queue_message"] is None and data["diagnostic_completed"] is False


@pytest.mark.parametrize("remove_first", [True, False])
def test_student_who_took_old_keeps_result_and_gets_new(env, remove_first):
    old, s, svc = _replace(env)
    old_attempt = _take(env, 1, old)
    if remove_first:
        svc.archive_diagnostic(old)  # admin "delete"
    new = _mk_diag(s, "new")
    svc.publish_assessment(new)  # archives a still-published old one itself
    assert s.get(Assessment, old).status == "archived"
    assert s.get(Assessment, old).superseded_at is not None

    at = env["attempt_service"]
    # old result still there
    assert at.get_attempt_results(old_attempt)["attempt_id"] == old_attempt
    assert any(a["attempt_id"] == old_attempt and a["status"] == "submitted" for a in at.list_student_attempts(1))

    data = _client(env, 1).get("/api/lms/diagnostics/default").get_json()["data"]
    assert data["id"] == new
    assert data["diagnostic_completed"] is True and data["attempted_this_diagnostic"] is False
    assert data["pending_count"] == 1 and data["queue_message"] is None
    assert [q["status"] for q in data["queue"]] == ["taken", "available"]

    # new one starts as a normal first attempt (no retake approval needed)
    attempt, resumed, done = at.start_attempt(1, new)
    assert not done and attempt.assessment_id == new
    # ...and reports as in progress so the student can resume it
    data = _client(env, 1).get("/api/lms/diagnostics/default").get_json()["data"]
    assert data["in_progress_attempt_id"] == attempt.id


def test_student_who_took_neither_gets_both_in_order(env):
    old, s, svc = _replace(env)
    svc.archive_diagnostic(old)
    new = _mk_diag(s, "new")
    svc.publish_assessment(new)
    at, c = env["attempt_service"], _client(env, 2)

    data = c.get("/api/lms/diagnostics/default").get_json()["data"]
    assert data["id"] == old  # first one first
    assert data["pending_count"] == 2
    assert "You have 2 diagnostic assessments" in data["queue_message"]
    assert [q["status"] for q in data["queue"]] == ["available", "locked"]

    # second cannot be started before the first
    with pytest.raises(LMSValidationError, match="earlier diagnostic"):
        at.start_attempt(2, new)
    r = c.post(f"/api/lms/quizzes/{new}/start", json={})
    assert r.status_code == 400

    # first (removed) one is startable through the API, and gets submitted
    r = c.post(f"/api/lms/quizzes/{old}/start", json={})
    assert r.status_code == 201, r.get_json()
    at.submit_attempt(r.get_json()["data"]["attempt_id"])

    data = c.get("/api/lms/diagnostics/default").get_json()["data"]
    assert data["id"] == new and data["attempted_this_diagnostic"] is False
    assert data["pending_count"] == 1 and data["queue_message"] is None
    assert [q["status"] for q in data["queue"]] == ["taken", "available"]

    _take(env, 2, new)
    data = c.get("/api/lms/diagnostics/default").get_json()["data"]
    assert data["id"] == new and data["attempted_this_diagnostic"] is True
    assert data["pending_count"] == 0
    assert len([a for a in at.list_student_attempts(2) if a["status"] == "submitted"]) == 2


def test_removed_without_replacement_is_not_offered(env):
    old, s, svc = _replace(env)
    svc.archive_diagnostic(old)
    assert env["diagnostic_service"].get_student_diagnostic_queue(3) == []
    assert _client(env, 3).get("/api/lms/diagnostics/default").status_code == 404
    with pytest.raises(LMSValidationError):
        env["attempt_service"].start_attempt(3, old)


def test_archived_draft_and_other_grade_are_never_queued(env):
    s, svc, ds = env["s"], env["assessment_service"], env["diagnostic_service"]
    draft = _mk_diag(s, "never published")
    svc.archive_diagnostic(draft)
    assert s.get(Assessment, draft).superseded_at is None
    other_old = _mk_diag(s, "grade 10 old", grade="10")
    svc.publish_assessment(other_old)
    svc.archive_diagnostic(other_old)
    legacy = _mk_diag(s, "archived before this feature", status="archived")  # superseded_at NULL
    current = _mk_diag(s, "current")
    svc.publish_assessment(current)

    assert [a.id for a in ds.get_student_diagnostic_queue(4)] == [current]
    for blocked in (draft, other_old, legacy):
        with pytest.raises(LMSValidationError):
            env["attempt_service"].start_attempt(4, blocked)


def test_republishing_a_removed_diagnostic_makes_it_current_again(env):
    old, s, svc = _replace(env)
    svc.archive_diagnostic(old)
    svc.publish_assessment(old)
    a = s.get(Assessment, old)
    assert a.status == "published" and a.superseded_at is None
    assert [x.id for x in env["diagnostic_service"].get_student_diagnostic_queue(5)] == [old]


def test_completed_diagnostic_still_returns_existing_attempt(env):
    """Unchanged behaviour: starting an already-submitted diagnostic hands back that attempt."""
    old, s, svc = _replace(env)
    first = _take(env, 6, old)
    attempt, resumed, done = env["attempt_service"].start_attempt(6, old)
    assert done and attempt.id == first
    assert s.query(AssessmentAttempt).filter_by(student_id=6).count() == 1


def test_new_student_only_sees_latest_not_pre_join_superseded(env, monkeypatch):
    """Brand-new account after a replace: only the current published diagnostic."""
    from datetime import datetime, timedelta

    old, s, svc = _replace(env)
    svc.archive_diagnostic(old)
    new = _mk_diag(s, "new")
    svc.publish_assessment(new)
    assert s.get(Assessment, old).superseded_at is not None

    # Student joined after old was already replaced.
    join_at = s.get(Assessment, old).superseded_at + timedelta(minutes=5)
    monkeypatch.setattr(
        env["diagnostic_service"], "_student_queue_anchor_at", lambda sid: join_at
    )
    ds = env["diagnostic_service"]
    assert [a.id for a in ds.get_student_diagnostic_queue(10)] == [new]
    assert [a.id for a in ds.get_student_pending_diagnostics(10)] == [new]


def test_existing_student_still_sees_superseded_after_replace(env, monkeypatch):
    """Student who existed before the replace still owes old then new."""
    from datetime import datetime, timedelta

    old, s, svc = _replace(env)
    svc.archive_diagnostic(old)
    new = _mk_diag(s, "new")
    svc.publish_assessment(new)

    join_at = (s.get(Assessment, old).superseded_at or datetime.utcnow()) - timedelta(days=1)
    monkeypatch.setattr(
        env["diagnostic_service"], "_student_queue_anchor_at", lambda sid: join_at
    )
    ds = env["diagnostic_service"]
    assert [a.id for a in ds.get_student_diagnostic_queue(11)] == [old, new]


def test_post_join_new_diagnostic_appears_for_recent_student(env, monkeypatch):
    """Student who joined at latest, then teacher publishes another: both show."""
    from datetime import datetime, timedelta

    old, s, svc = _replace(env)
    # Student joins while OLD is the only published diagnostic.
    join_at = datetime.utcnow() - timedelta(hours=1)
    monkeypatch.setattr(
        env["diagnostic_service"], "_student_queue_anchor_at", lambda sid: join_at
    )
    ds = env["diagnostic_service"]
    assert [a.id for a in ds.get_student_diagnostic_queue(12)] == [old]

    new = _mk_diag(s, "newer")
    svc.publish_assessment(new)
    # OLD superseded after join → still in queue; NEW is current.
    assert [a.id for a in ds.get_student_diagnostic_queue(12)] == [old, new]

    # Brand-new student after NEW is published → latest only.
    later = (s.get(Assessment, old).superseded_at or datetime.utcnow()) + timedelta(minutes=1)
    monkeypatch.setattr(
        env["diagnostic_service"], "_student_queue_anchor_at", lambda sid: later
    )
    assert [a.id for a in ds.get_student_diagnostic_queue(13)] == [new]
