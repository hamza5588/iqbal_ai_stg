"""Learning Chat path refresh behavior after practice completion."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.lms_models import (
    AssessmentAttempt,
    Base,
    LearningPath,
    LearningPathItem,
    StudentProfile,
)


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        AssessmentAttempt.__table__, LearningPath.__table__,
        LearningPathItem.__table__, StudentProfile.__table__,
    ])
    s = sessionmaker(bind=engine)()

    from app.services.lms import learning_path_service as lps
    from app.services.lms import path_generator, student_profile_service, performance_service

    monkeypatch.setattr(lps, "get_db", lambda: s)
    monkeypatch.setattr(student_profile_service, "get_db", lambda: s)
    # keep ensure_learning_path from doing mastery work
    state = {"weak": [{"topic_id": 5, "topic_name": "Geometry"}]}
    monkeypatch.setattr(path_generator, "has_mastery_data", lambda sid: True)
    monkeypatch.setattr(path_generator, "get_weak_topics", lambda sid: state["weak"])
    monkeypatch.setattr(performance_service, "rebuild_student_mastery", lambda sid: None)
    monkeypatch.setattr(performance_service, "repair_diagnostic_topic_meta", lambda a: False)
    return s, lps, state


def _mk_path(db, sid, pid, status, item_status, created):
    p = LearningPath(id=pid, student_id=sid, title="Personalized Learning Path", status=status, created_at=created)
    db.add(p)
    db.add(LearningPathItem(learning_path_id=pid, item_type="practice", item_id=0, sort_order=0, status=item_status))
    db.commit()
    return p


def test_completed_practice_regenerates_when_practice_topics_remain(db):
    s, lps, state = db
    s.add(StudentProfile(user_id=1, diagnostic_completed=True))
    s.add(AssessmentAttempt(id=1, student_id=1, assessment_id=44, status="submitted",
                            submitted_at=datetime(2026, 9, 4, 6, 0)))
    _mk_path(s, 1, 10, "completed", "completed", datetime(2026, 9, 4, 6, 10))
    s.commit()

    d = lps.ensure_learning_path(1)
    assert d["id"] != 10
    assert d["status"] == "active"
    assert d["completed_count"] == 0 and d["total_count"] == 1
    assert s.query(LearningPath).count() == 2


def test_completed_practice_path_stays_done_when_no_practice_topics_remain(db):
    s, lps, state = db
    state["weak"] = []
    s.add(StudentProfile(user_id=4, diagnostic_completed=True))
    s.add(AssessmentAttempt(id=4, student_id=4, assessment_id=44, status="submitted",
                            submitted_at=datetime(2026, 9, 4, 6, 0)))
    _mk_path(s, 4, 40, "completed", "completed", datetime(2026, 9, 4, 6, 10))
    s.commit()

    d = lps.ensure_learning_path(4)
    assert d["id"] == 40
    assert d["status"] == "completed"
    assert s.query(LearningPath).count() == 1


def test_heals_redundant_regen_stacked_on_completed_path(db):
    s, lps, state = db
    state["weak"] = []
    s.add(StudentProfile(user_id=2, diagnostic_completed=True, current_learning_path_id=21))
    s.add(AssessmentAttempt(id=2, student_id=2, assessment_id=44, status="submitted",
                            submitted_at=datetime(2026, 9, 4, 6, 0)))
    _mk_path(s, 2, 20, "completed", "completed", datetime(2026, 9, 4, 6, 10))
    _mk_path(s, 2, 21, "active", "pending", datetime(2026, 9, 4, 6, 11))  # the bogus regen
    s.commit()

    d = lps.ensure_learning_path(2)
    assert d["id"] == 20 and d["status"] == "completed"
    assert s.query(LearningPath).filter_by(id=21).one().status == "archived"
    assert s.query(LearningPath).count() == 2
    # stable on a second call
    d2 = lps.ensure_learning_path(2)
    assert d2["id"] == d["id"]


def test_pending_path_before_any_practice_stays_pending(db):
    s, lps, state = db
    s.add(StudentProfile(user_id=3, diagnostic_completed=True))
    s.add(AssessmentAttempt(id=3, student_id=3, assessment_id=44, status="submitted",
                            submitted_at=datetime(2026, 9, 4, 6, 0)))
    _mk_path(s, 3, 30, "active", "pending", datetime(2026, 9, 4, 6, 10))
    s.commit()

    d = lps.ensure_learning_path(3)
    assert d["id"] == 30 and d["status"] == "active"
    assert d["completed_count"] == 0
    assert s.query(LearningPath).count() == 1
