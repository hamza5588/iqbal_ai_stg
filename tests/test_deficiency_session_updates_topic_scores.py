"""
Tests for update_topic_scores_from_deficiency_session() (app/services/lms/
performance_service.py). Before this fix, completing a Learning Chat
(deficiency practice) session never touched StudentTopicScore - "Overall
Progress" / "Weak Topics" (both read purely from StudentTopicScore) never
moved no matter how well a student did in practice, and
learning_path_service.ensure_learning_path() kept regenerating an identical
"practice weak areas" step right after the student finished one, since the
practiced topics never stopped looking weak.
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.lms_models import Base, DeficiencyChatSession, StudentTopicScore


@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[DeficiencyChatSession.__table__, StudentTopicScore.__table__]
    )
    Session = sessionmaker(bind=engine)
    session = Session()

    # performance_service does `from app.utils.db import get_db` at module
    # scope, binding its own name to the original function - patching
    # app.utils.db.get_db alone would not affect that already-bound name, so
    # patch it directly where performance_service looks it up.
    from app.services.lms import performance_service
    monkeypatch.setattr(performance_service, "get_db", lambda: session)

    yield session
    session.close()


def _make_session(db_session, questions, student_id=42, session_id=1):
    row = DeficiencyChatSession(
        id=session_id,
        student_id=student_id,
        questions_json=json.dumps(questions),
        current_index=len(questions),
        correct_count=sum(1 for q in questions if q.get("correct")),
        status="completed",
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_perfect_practice_marks_topic_mastered(db_session):
    """The exact reported symptom: 10/10 correct on one topic must actually
    move that topic's score, not leave it stuck at 0%/weak."""
    from app.services.lms import performance_service

    questions = [{"topic_id": 5, "answered": True, "correct": True} for _ in range(10)]
    _make_session(db_session, questions, student_id=42, session_id=1)

    performance_service.update_topic_scores_from_deficiency_session(1)

    score = db_session.query(StudentTopicScore).filter_by(student_id=42, topic_id=5).first()
    assert score is not None
    assert score.score_percent == 100.0
    assert score.mastery_status == "mastered"


def test_partial_correctness_computes_per_topic_percentage(db_session):
    from app.services.lms import performance_service

    # Two topics: topic 1 gets 1/2 correct (50%, still weak), topic 2 gets
    # 3/3 correct (100%, mastered). Unanswered/skipped questions must not
    # count toward the denominator.
    questions = [
        {"topic_id": 1, "answered": True, "correct": True},
        {"topic_id": 1, "answered": True, "correct": False},
        {"topic_id": 2, "answered": True, "correct": True},
        {"topic_id": 2, "answered": True, "correct": True},
        {"topic_id": 2, "answered": True, "correct": True},
        {"topic_id": 3, "answered": False, "correct": False},  # never reached
    ]
    _make_session(db_session, questions, student_id=99, session_id=2)

    performance_service.update_topic_scores_from_deficiency_session(2)

    scores = {
        s.topic_id: s
        for s in db_session.query(StudentTopicScore).filter_by(student_id=99).all()
    }
    assert scores[1].score_percent == 50.0
    assert scores[1].mastery_status == "weak"
    assert scores[2].score_percent == 100.0
    assert scores[2].mastery_status == "mastered"
    assert 3 not in scores  # unanswered topic never touched


def test_updates_existing_score_not_just_creates_new(db_session):
    """A student who was 'weak' (0%) before must actually improve, not get a
    second, ignored row - StudentTopicScore is unique on (student_id, topic_id)."""
    from app.services.lms import performance_service

    db_session.add(StudentTopicScore(student_id=7, topic_id=1, score_percent=0.0, mastery_status="weak"))
    db_session.commit()

    questions = [{"topic_id": 1, "answered": True, "correct": True} for _ in range(5)]
    _make_session(db_session, questions, student_id=7, session_id=3)

    performance_service.update_topic_scores_from_deficiency_session(3)

    rows = db_session.query(StudentTopicScore).filter_by(student_id=7, topic_id=1).all()
    assert len(rows) == 1  # updated in place, not duplicated
    assert rows[0].score_percent == 100.0
    assert rows[0].mastery_status == "mastered"


def test_missing_session_is_a_noop(db_session):
    from app.services.lms import performance_service

    performance_service.update_topic_scores_from_deficiency_session(9999)  # must not raise
    assert db_session.query(StudentTopicScore).count() == 0


# ---------------------------------------------------------------------------
# get_overall_progress() must be weighted by sample_size, not a flat average
# across topics - a diagnostic's topic assignment is a text/PDF-label
# heuristic, so real question counts per topic are often wildly uneven.
# Reproduced live: a diagnostic scored 64% raw came out as 95% unweighted.
# ---------------------------------------------------------------------------

def test_overall_progress_weighted_by_sample_size(db_session):
    from app.services.lms import performance_service

    # One topic resolved from just 1 question (100%, by chance), four
    # topics resolved from many questions each, mostly wrong. An unweighted
    # average would let the lone 1-question topic pull the score way up.
    db_session.add_all([
        StudentTopicScore(student_id=1, topic_id=1, score_percent=100.0, sample_size=1, mastery_status="mastered"),
        StudentTopicScore(student_id=1, topic_id=2, score_percent=20.0, sample_size=10, mastery_status="weak"),
        StudentTopicScore(student_id=1, topic_id=3, score_percent=20.0, sample_size=10, mastery_status="weak"),
    ])
    db_session.commit()

    # Weighted: (100*1 + 20*10 + 20*10) / (1+10+10) = 500/21 ≈ 23.81
    # Unweighted (the old, buggy behavior) would give (100+20+20)/3 = 46.67
    progress = performance_service.get_overall_progress(1)
    assert progress == round(500.0 / 21, 2)
    assert progress < 25.0  # nowhere near the misleading unweighted 46.67


def test_overall_progress_falls_back_to_unweighted_when_no_sample_size(db_session):
    """Legacy rows backfilled to sample_size=1 (equal weight) must not error
    or divide by zero - same as an unweighted average in that case."""
    from app.services.lms import performance_service

    db_session.add_all([
        StudentTopicScore(student_id=2, topic_id=1, score_percent=100.0, sample_size=1, mastery_status="mastered"),
        StudentTopicScore(student_id=2, topic_id=2, score_percent=0.0, sample_size=1, mastery_status="weak"),
    ])
    db_session.commit()

    assert performance_service.get_overall_progress(2) == 50.0
