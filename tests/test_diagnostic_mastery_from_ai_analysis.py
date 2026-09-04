"""
Diagnostic mastery must be built from the SAME AI weak/strong grouping the
student sees on the results screen - not a separate per-question fuzzy
text->topic heuristic.

Live-reproduced bug: a student scored 40% raw on the platform diagnostic;
every question had questions.topic_id = NULL, so the fuzzy resolver mapped
only ~5 (all-wrong) questions to topics and wrote a uniform 0% mastery
profile. "Overall Progress" showed 0% for a 40% diagnostic.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.lms_models import (
    AssessmentAttempt,
    AttemptAnswer,
    Base,
    DeficiencyChatSession,
    StudentTopicScore,
    Topic,
)


@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            AssessmentAttempt.__table__,
            AttemptAnswer.__table__,
            DeficiencyChatSession.__table__,
            StudentTopicScore.__table__,
            Topic.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    from app.services.lms import performance_service

    monkeypatch.setattr(performance_service, "get_db", lambda: session)
    yield session
    session.close()


def test_buckets_come_from_ai_all_topics_and_use_real_answers(db_session, monkeypatch):
    from app.services.lms import performance_service

    attempt = AssessmentAttempt(
        id=1, student_id=42, assessment_id=44, status="submitted", score=10, max_score=25
    )
    db_session.add(attempt)
    # 4 questions: q1,q2 correct (Number Sense), q3,q4 wrong (Geometry)
    db_session.add_all([
        AttemptAnswer(attempt_id=1, question_id=1, selected_option_index=0, is_correct=True),
        AttemptAnswer(attempt_id=1, question_id=2, selected_option_index=0, is_correct=True),
        AttemptAnswer(attempt_id=1, question_id=3, selected_option_index=0, is_correct=False),
        AttemptAnswer(attempt_id=1, question_id=4, selected_option_index=0, is_correct=False),
    ])
    db_session.commit()

    # _topic_buckets_from_diagnostic_analysis does a late
    # `from app.services.lms.weakness_analyzer import analyze_diagnostic_attempt`
    import app.services.lms.weakness_analyzer as wa
    monkeypatch.setattr(wa, "analyze_diagnostic_attempt", lambda attempt_id: {
        "all_topics": [
            {"topic_id": 96, "topic_name": "Number Sense", "score_percent": 100.0, "question_ids": [1, 2]},
            {"topic_id": 5, "topic_name": "Geometry", "score_percent": 0.0, "question_ids": [3, 4]},
        ],
        "weak_topics": [], "strong_topics": [],
    })

    class _A:  # minimal assessment stub
        assessment_type = "diagnostic"
        creation_mode = "pdf_qa_auto"
        questions = []
        title = "unit22"
        description = None

    buckets = performance_service._topic_buckets_from_diagnostic_analysis(attempt, _A())
    assert buckets == {96: {"correct": 2, "total": 2}, 5: {"correct": 0, "total": 2}}


def test_upsert_topic_score_blend_math(db_session):
    from datetime import datetime
    from app.services.lms import performance_service

    db_session.add(
        StudentTopicScore(student_id=1, topic_id=5, score_percent=0.0, sample_size=6, mastery_status="weak")
    )
    db_session.commit()

    performance_service._upsert_topic_score(
        db_session, 1, 5, 100.0, 2, datetime.utcnow(), blend=True
    )
    db_session.commit()
    row = db_session.query(StudentTopicScore).filter_by(student_id=1, topic_id=5).first()
    assert row.score_percent == pytest.approx(200.0 / 8, abs=0.01)
    assert row.sample_size == 8

    performance_service._upsert_topic_score(
        db_session, 1, 5, 90.0, 4, datetime.utcnow(), blend=False
    )
    db_session.commit()
    row = db_session.query(StudentTopicScore).filter_by(student_id=1, topic_id=5).first()
    assert row.score_percent == 90.0  # replace, not blend
    assert row.sample_size == 4


def test_rebuild_clears_stale_rows_before_recompute(db_session, monkeypatch):
    """Pre-fix accounts have StudentTopicScore rows on topic_ids the new AI
    grouping never touches (phantom weak topics that never clear). A rebuild
    must drop them, not leave them alongside the fresh rows."""
    from datetime import datetime
    from app.services.lms import performance_service

    # stale pre-fix rows on topics 1 & 17 - nothing will ever re-touch these
    db_session.add_all([
        StudentTopicScore(student_id=50, topic_id=1, score_percent=0.0, sample_size=1,
                          mastery_status="weak", updated_at=datetime(2026, 9, 4, 3, 35)),
        StudentTopicScore(student_id=50, topic_id=17, score_percent=0.0, sample_size=1,
                          mastery_status="weak", updated_at=datetime(2026, 9, 4, 3, 35)),
    ])
    db_session.commit()

    # no attempts / sessions for this student -> rebuild just clears
    monkeypatch.setattr(performance_service, "update_topic_scores_from_attempt", lambda *_: None)
    performance_service.rebuild_student_mastery(50)

    assert db_session.query(StudentTopicScore).filter_by(student_id=50).count() == 0


def test_find_similar_topic_reuses_synonym_variants():
    from app.services.lms import topic_resolver

    class T:
        def __init__(self, tid, name):
            self.id, self.name = tid, name

    existing = [T(96, "Number Sense"), T(4, "Algebra"), T(5, "Geometry")]

    # appended-token drift -> reuse
    assert topic_resolver._find_similar_topic(existing, "Number Sense Arithmetic").id == 96
    assert topic_resolver._find_similar_topic(existing, "Algebra Equations").id == 4
    # genuinely different concept -> no reuse
    assert topic_resolver._find_similar_topic(existing, "Trigonometry Ratios") is None
