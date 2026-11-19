"""Unit tests for quiz / Learning Chat / AI-Guided Practice dwell aggregation."""

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database_models import Base, User
from app.models.lms_models import (
    AIGPPdfVersion,
    AIGPQuestion,
    AIGPSession,
    AIGPSessionItem,
    AIGPTopic,
    Assessment,
    AssessmentAttempt,
    AssessmentQuestion,
    AttemptAnswer,
    DeficiencyChatSession,
    GuidedPracticeAttempt,
    Question,
    TargetBankDocument,
    TargetBankItem,
    Topic,
)
from app.services.lms import time_tracking_service as tts


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _student(db, user_id=2):
    u = User(
        id=user_id,
        username=f"s{user_id}",
        full_name=f"Student {user_id}",
        school_name="School A",
        useremail=f"s{user_id}@example.com",
        password="x",
        role="student",
        class_standard="9",
        medium="English",
        groq_api_key="x",
    )
    db.add(u)
    db.flush()
    return u


def test_seconds_helpers():
    assert tts.seconds_to_hms(0) == "0:00:00"
    assert tts.seconds_to_hms(65) == "0:01:05"
    assert tts.seconds_to_hms(3661) == "1:01:01"
    assert tts.seconds_to_minutes(90) == 1.5
    assert tts._nonneg_seconds(-12) == 0
    assert tts._nonneg_seconds("40") == 40
    assert tts._nonneg_seconds("nope") == 0


def test_quiz_clamps_negative_dwell_and_nulls_inverted_wall(db):
    _student(db)
    topic = Topic(name="Clamp", slug="clamp-tt", subject="Mathematics", grade_level="9")
    db.add(topic)
    db.flush()
    q1 = Question(
        topic_id=topic.id,
        question_text="Q1?",
        options_json='[{"label":"A","text":"1"},{"label":"B","text":"2"},{"label":"C","text":"3"},{"label":"D","text":"4"}]',
        correct_option_index=0,
        difficulty="easy",
        source_type="manual",
    )
    assessment = Assessment(title="Clamp Quiz", assessment_type="quiz", creation_mode="manual", status="published")
    db.add_all([q1, assessment])
    db.flush()
    db.add(AssessmentQuestion(assessment_id=assessment.id, question_id=q1.id, sort_order=1))
    attempt = AssessmentAttempt(
        student_id=2,
        assessment_id=assessment.id,
        status="submitted",
        started_at=datetime(2026, 10, 29, 8, 0),
        submitted_at=datetime(2026, 10, 29, 7, 0),  # inverted
    )
    db.add(attempt)
    db.flush()
    answer = AttemptAnswer(
        attempt_id=attempt.id, question_id=q1.id, selected_option_index=0, is_correct=True, time_spent_seconds=-9
    )
    db.add(answer)
    db.flush()
    timing = tts.quiz_attempt_timing(attempt, [answer], [q1.id])
    assert timing["wall_clock_seconds"] is None
    assert timing["question_dwell_total_seconds"] == 0
    assert timing["questions"][0]["time_spent_seconds"] == 0

    chat = DeficiencyChatSession(
        student_id=2,
        questions_json='[{"id":1,"time_spent_seconds":-5},{"id":2,"time_spent_seconds":12}]',
        status="completed",
    )
    assert tts.learning_chat_session_timing(chat)["total_time_spent_seconds"] == 12


def test_quiz_attempt_timing_wall_clock_and_per_mcq(db):
    _student(db)
    topic = Topic(name="Algebra", slug="alg-tt", subject="Mathematics", grade_level="9")
    db.add(topic)
    db.flush()
    q1 = Question(
        topic_id=topic.id,
        question_text="Q1?",
        options_json='[{"label":"A","text":"1"},{"label":"B","text":"2"},{"label":"C","text":"3"},{"label":"D","text":"4"}]',
        correct_option_index=1,
        difficulty="easy",
        source_type="manual",
    )
    q2 = Question(
        topic_id=topic.id,
        question_text="Q2?",
        options_json='[{"label":"A","text":"1"},{"label":"B","text":"2"},{"label":"C","text":"3"},{"label":"D","text":"4"}]',
        correct_option_index=0,
        difficulty="easy",
        source_type="manual",
    )
    assessment = Assessment(title="Quiz", assessment_type="quiz", creation_mode="manual", status="published")
    db.add_all([q1, q2, assessment])
    db.flush()
    db.add_all(
        [
            AssessmentQuestion(assessment_id=assessment.id, question_id=q1.id, sort_order=1),
            AssessmentQuestion(assessment_id=assessment.id, question_id=q2.id, sort_order=2),
        ]
    )
    attempt = AssessmentAttempt(
        student_id=2,
        assessment_id=assessment.id,
        status="submitted",
        started_at=datetime(2026, 10, 29, 7, 0),
        submitted_at=datetime(2026, 10, 29, 7, 20),
        score=1,
        max_score=2,
    )
    db.add(attempt)
    db.flush()
    a1 = AttemptAnswer(
        attempt_id=attempt.id, question_id=q1.id, selected_option_index=1, is_correct=True, time_spent_seconds=40
    )
    a2 = AttemptAnswer(
        attempt_id=attempt.id, question_id=q2.id, selected_option_index=0, is_correct=True, time_spent_seconds=80
    )
    db.add_all([a1, a2])
    db.flush()

    timing = tts.quiz_attempt_timing(attempt, [a1, a2], [q1.id, q2.id])
    assert timing["wall_clock_seconds"] == 1200
    assert timing["wall_clock_minutes"] == 20.0
    assert timing["question_dwell_total_seconds"] == 120
    assert [q["time_spent_seconds"] for q in timing["questions"]] == [40, 80]


def test_learning_chat_and_guided_practice_window_totals(db):
    _student(db)
    chat = DeficiencyChatSession(
        student_id=2,
        questions_json='[{"id":1,"time_spent_seconds":30},{"id":2,"time_spent_seconds":90}]',
        status="completed",
        created_at=datetime(2026, 10, 28, 8, 0),
        updated_at=datetime(2026, 10, 28, 9, 0),
    )
    db.add(chat)
    db.flush()

    doc = TargetBankDocument(
        sha256="abc123",
        original_filename="bank.pdf",
        grade_level="9",
        storage_path="/tmp/bank.pdf",
        extractor_version="test",
        stats_json="{}",
    )
    db.add(doc)
    db.flush()
    topic = Topic(name="Numbers", slug="num-tt", subject="Mathematics", grade_level="9")
    db.add(topic)
    db.flush()
    question = Question(
        topic_id=topic.id,
        question_text="Bank Q?",
        options_json='[{"label":"A","text":"1"},{"label":"B","text":"2"},{"label":"C","text":"3"},{"label":"D","text":"4"}]',
        correct_option_index=0,
        difficulty="easy",
        source_type="manual",
        is_active=False,
    )
    db.add(question)
    db.flush()
    item = TargetBankItem(
        document_id=doc.id,
        question_id=question.id,
        source_question_number=1,
        verification_status="approved",
    )
    db.add(item)
    db.flush()
    db.add(
        GuidedPracticeAttempt(
            student_id=2,
            bank_item_id=item.id,
            status="answered",
            selected_original_index=0,
            is_correct=True,
            answered_at=datetime(2026, 10, 28, 10, 0),
            time_spent_seconds=45,
            option_order_json="[0,1,2,3]",
        )
    )

    version = AIGPPdfVersion(
        rag_thread_id="thread-tt",
        grade_level="9",
        original_filename="target.pdf",
        status="ready",
    )
    db.add(version)
    db.flush()
    aigp_topic = AIGPTopic(version_id=version.id, title="Exponents", sort_order=1)
    db.add(aigp_topic)
    db.flush()
    aigp_q = AIGPQuestion(
        version_id=version.id,
        topic_id=aigp_topic.id,
        question_text="AIGP?",
        options_json='[{"label":"A","text":"1"},{"label":"B","text":"2"},{"label":"C","text":"3"},{"label":"D","text":"4"}]',
        correct_option_index=1,
        dedupe_key="aigp-q1",
    )
    db.add(aigp_q)
    db.flush()
    session = AIGPSession(
        student_id=2,
        version_id=version.id,
        topic_id=aigp_topic.id,
        status="completed",
        question_count=1,
        answered_count=1,
        correct_count=1,
        completed_at=datetime(2026, 10, 28, 11, 0),
    )
    db.add(session)
    db.flush()
    db.add(
        AIGPSessionItem(
            session_id=session.id,
            question_id=aigp_q.id,
            position=1,
            option_order_json="[0,1,2,3]",
            selected_original_index=1,
            is_correct=True,
            answered_at=datetime(2026, 10, 28, 11, 0),
            time_spent_seconds=75,
        )
    )
    db.commit()

    activity = tts.activity_times_for_window(db, [2], date(2026, 10, 27), date(2026, 10, 29))[2]
    assert activity["learning_chat_seconds"] == 120
    assert activity["learning_chat_hms"] == "0:02:00"
    assert activity["ai_guided_practice_seconds"] == 120  # 45 + 75
    assert activity["ai_guided_practice_hms"] == "0:02:00"
    assert len(activity["learning_chat"]["questions"]) == 2
    assert len(activity["ai_guided_practice"]["questions"]) == 2
