"""Metadata persistence and deterministic adaptive practice regression tests."""
from types import SimpleNamespace
from collections import Counter
from datetime import datetime, timedelta
import json

import pytest
from flask import Flask
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.services.quiz.models import MCQQuestion
from app.services.quiz.mcq_converter import mcq_to_question_fields


def mcq(**metadata):
    return MCQQuestion(
        question_text="What is 2 + 3?",
        options=[{"label": label, "text": str(n)} for label, n in zip("ABCD", [4, 5, 6, 7])],
        correct_option_label="B", preserve_option_order=True, **metadata,
    )


def test_all_four_fields_survive_conversion():
    fields = mcq_to_question_fields(mcq(domain="Numbers", topic="Addition",
                                       cognitive_level="Application", difficulty="hard"))
    assert {k: fields.get(k) for k in ("domain", "topic_label", "cognitive_level", "difficulty")} == {
        "domain": "Numbers", "topic_label": "Addition", "cognitive_level": "Application", "difficulty": "hard",
    }


def test_hybrid_adapter_keeps_topic_and_cognitive_level():
    from app.services.quiz.hybrid_mcq_extractor import vision_mcq_to_question
    raw = SimpleNamespace(question="What is 2 + 3?", options=[SimpleNamespace(label=l, text=str(n)) for l, n in zip("ABCD", [4, 5, 6, 7])],
                          correct_answer="B", domain="Numbers", topic="Addition",
                          cognitive_level="Application", difficulty="Intermediate")
    result = vision_mcq_to_question(raw)
    assert result.topic == "Addition"
    assert result.cognitive_level == "Application"
    assert result.difficulty == "medium"


def profile(topic="Fractions", cognitive="Application", difficulty="medium", domain="Numbers"):
    return dict(domain=domain, topic=topic, cognitive_level=cognitive, difficulty=difficulty)


def test_exact_matches_reserved_before_near_matches():
    from app.services.lms.adaptive_practice_service import select_candidates
    profiles = [profile(cognitive="Knowledge"), profile()]
    candidates = [dict(profile(), question_id=1), dict(profile(difficulty="hard"), question_id=2),
                  dict(profile(domain="Algebra", cognitive="Knowledge"), question_id=3)]
    selected = select_candidates(candidates, profiles)
    assert selected[1]["question_id"] == 1
    assert selected[0]["question_id"] == 2


def test_case_whitespace_normalization_and_domain_floor():
    from app.services.lms.adaptive_practice_service import rank_candidate
    assert not any(rank_candidate(profile(domain=" numbers ", topic=" FRACTIONS "), profile()))
    assert rank_candidate(profile(domain="Algebra"), profile()) is None
    assert rank_candidate(profile(domain=None), profile()) is None


def test_same_domain_fallback_prefers_related_topic():
    from app.services.lms.adaptive_practice_service import select_candidates
    candidates = [dict(profile(topic="Logarithms"), question_id=1),
                  dict(profile(topic="Adding Fractions"), question_id=2)]
    assert select_candidates(candidates, [profile()])[0]["question_id"] == 2


def test_mixed_wrong_levels_get_proportional_slots_above_five():
    from app.services.lms.adaptive_practice_service import desired_profiles
    result = desired_profiles([profile(), profile(), profile(cognitive="Knowledge", difficulty="hard")], 7)
    assert len(result) == 7
    assert Counter((p["cognitive_level"], p["difficulty"]) for p in result) == {
        ("application", "medium"): 5, ("knowledge", "hard"): 2,
    }


@pytest.fixture()
def db(monkeypatch):
    from app.models.lms_models import Base
    import app.utils.db as db_module
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(db_module, "get_session_factory", lambda: lambda: session)
    app = Flask(__name__)
    with app.app_context():
        yield session
    session.close()
    engine.dispose()


def store_question(db, n=1, metadata=None, **extra):
    from app.models.lms_models import Question
    meta = metadata or profile()
    row = Question(question_text=f"What is {n} + 10?", correct_option_index=0,
                   options_json=json.dumps([dict(label=l, text=str(n + k)) for l, k in zip("ABCD", (10, 11, 12, 13))]),
                   domain=meta["domain"], topic_label=meta["topic"], cognitive_level=meta["cognitive_level"],
                   difficulty=meta["difficulty"], **extra)
    db.add(row)
    db.flush()
    return row


def assessment(db, kind, metadata, correct, when=0):
    from app.models.lms_models import Assessment, AssessmentQuestion, AssessmentAttempt, AttemptAnswer
    paper = Assessment(title="Test", assessment_type=kind, status="published", creation_mode="manual")
    db.add(paper)
    db.flush()
    attempt = AssessmentAttempt(student_id=1, assessment_id=paper.id, status="submitted",
                                submitted_at=datetime(2026, 10, 1) + timedelta(minutes=when))
    db.add(attempt)
    db.flush()
    for i, (meta, ok) in enumerate(zip(metadata, correct)):
        question = store_question(db, i + 100 * paper.id, meta)
        db.add(AssessmentQuestion(assessment_id=paper.id, question_id=question.id, sort_order=i))
        db.add(AttemptAnswer(attempt_id=attempt.id, question_id=question.id,
                             selected_option_index=0 if ok else 1, is_correct=ok))
    db.commit()
    return attempt


@pytest.mark.parametrize("kind", ["diagnostic", "quiz"])
def test_both_assessments_retrieve_wrong_metadata_and_update_mastery(db, kind):
    from app.services.lms.performance_service import analyze_attempt, update_topic_scores_from_attempt
    from app.models.lms_models import StudentTopicScore
    attempt = assessment(db, kind, [profile(), profile(cognitive="Knowledge"), profile()], [False, False, True])
    analysis = analyze_attempt(attempt.id)
    group = analysis["weak_topics"][0]
    assert group["wrong_count"] == 2 and group["total"] == 3
    assert [m["cognitive_level"] for m in group["wrong_metadata"]] == ["Application", "Knowledge"]
    update_topic_scores_from_attempt(attempt.id)
    score = db.query(StudentTopicScore).one()
    assert score.score_percent == pytest.approx(100 / 3)
    assert score.sample_size == 3


def test_latest_quiz_replaces_its_topics_and_keeps_unassessed_weaknesses(db):
    from app.services.lms.adaptive_practice_service import weak_groups_for_student
    from app.services.lms.performance_service import update_topic_scores_from_attempt
    from app.models.lms_models import StudentTopicScore
    first = assessment(db, "diagnostic", [profile(), profile(topic="Logarithms")], [False, False])
    update_topic_scores_from_attempt(first.id)
    second = assessment(db, "quiz", [profile(cognitive="Knowledge", difficulty="hard")], [False], when=1)
    update_topic_scores_from_attempt(second.id)
    live = [{"topic_id": r.topic_id} for r in db.query(StudentTopicScore)]
    groups = weak_groups_for_student(1, live)
    fractions = next(g for g in groups if g["topic_name"] == "Fractions")
    assert fractions["attempt_id"] == second.id
    assert fractions["wrong_metadata"][0]["difficulty"] == "hard"
    assert {g["topic_name"] for g in groups} == {"Fractions", "Logarithms"}
    third = assessment(db, "quiz", [profile()], [True], when=2)
    update_topic_scores_from_attempt(third.id)
    live = [{"topic_id": r.topic_id} for r in db.query(StudentTopicScore) if r.score_percent < 100]
    assert [g["topic_name"] for g in weak_groups_for_student(1, live)] == ["Logarithms"]


def test_metadata_persists_in_database_and_api_serialization(db):
    from app.services.lms.question_bank_service import create_question, question_to_dict
    fields = mcq_to_question_fields(mcq(**profile()))
    question = create_question(created_by=None, **fields)
    db.expire_all()
    result = question_to_dict(question)
    assert result["domain"] == "Numbers"
    assert result["topic_label"] == "Fractions"
    assert result["cognitive_level"] == "Application"


def test_migration_is_repeatable_on_disposable_legacy_database():
    from app.services.lms.question_metadata_migration import migrate_question_metadata
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE questions (id INTEGER PRIMARY KEY, question_text TEXT)"))
        conn.execute(text("INSERT INTO questions VALUES (1, 'kept')"))
    migrate_question_metadata(engine)
    migrate_question_metadata(engine)
    assert {"domain", "topic_label", "cognitive_level"} <= {c["name"] for c in inspect(engine).get_columns("questions")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT question_text FROM questions")).scalar() == "kept"
    engine.dispose()


@pytest.mark.parametrize("bank_count,expected_ai", [(7, 0), (3, 4), (0, 7)])
def test_target_priority_exact_quantity_and_ai_only_for_shortfall(db, monkeypatch, bank_count, expected_ai):
    from app.services.lms.adaptive_practice_service import build_queue
    from app.services.quiz import remediation_generator
    monkeypatch.setattr("app.services.lms.target_question_service.ensure_target_questions", lambda *a: None)
    monkeypatch.setattr("app.services.quiz.mcq_answer_check.verify_mcq_keys", lambda items: items)
    for n in range(bank_count):
        store_question(db, n, source_pdf_thread_id="target", created_by=9)
    store_question(db, 50, profile(domain="Algebra"), source_pdf_thread_id="target", created_by=9)
    store_question(db, 51, source_pdf_thread_id="other-teacher", created_by=10)
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return [mcq(**profile(domain=kwargs["domain"], topic=kwargs["topic_name"],
                              cognitive=kwargs["cognitive_level"], difficulty=kwargs["difficulty"])).model_copy(
                                  update={"question_text": f"Generated question {i}"}) for i in range(kwargs["count"])]
    monkeypatch.setattr(remediation_generator, "generate_remediation_mcqs", generate)
    entry = dict(topic_id=1, topic_name="Fractions", score_percent=30, wrong_metadata=[profile()] * 3)
    queue = build_queue(entry, ["target"], 9, 7)
    assert len(queue) == 7
    assert sum(q["source"] == "target_pdf" for q in queue) == bank_count
    assert sum(c["count"] for c in calls) == expected_ai
    assert all(q["domain"].casefold() == "numbers" for q in queue)


def test_wrong_domain_ai_is_rejected_instead_of_relabelled(db, monkeypatch):
    from app.services.lms.adaptive_practice_service import build_queue
    from app.services.lms.exceptions import LMSValidationError
    monkeypatch.setattr("app.services.lms.target_question_service.ensure_target_questions", lambda *a: None)
    monkeypatch.setattr("app.services.quiz.remediation_generator.generate_remediation_mcqs", lambda **k: [mcq(**profile(domain="Algebra"))])
    monkeypatch.setattr("app.services.quiz.mcq_answer_check.verify_mcq_keys", lambda items: items)
    entry = dict(topic_id=1, topic_name="Fractions", score_percent=0, wrong_metadata=[profile()])
    with pytest.raises(LMSValidationError, match="verified practice"):
        build_queue(entry, ["target"], 9, 1)


def test_supplied_diagnostic_pdf_has_all_twenty_printed_metadata_records():
    from pathlib import Path
    import pymupdf
    from app.services.quiz.question_metadata import printed_metadata
    path = Path(__file__).resolve().parents[2] / "DIL_SAATHI_Baseline_Diagnostic_Mathematics IX (1).pdf"
    if not path.exists():
        pytest.skip("Local sample PDF is not included in the repository")
    with pymupdf.open(path) as document:
        metadata = printed_metadata("\n".join(p.get_text() for p in document))
    assert set(metadata) == set(range(1, 21))
    assert metadata[1] == profile(topic="Classification of Real Numbers", cognitive="Knowledge")
    assert metadata[20] == profile(domain="Algebra", topic="Factor Theorem", cognitive="Analysis/Reasoning")


def test_learning_chat_serves_all_seven_and_quiz_invalidates_old_session(db, monkeypatch):
    from app.services.lms import deficiency_chat_service as chat, performance_service as perf
    from app.services.lms.attempt_service import _run_post_submit_steps
    from app.services.lms.assessment_service import get_assessment
    from app.models.lms_models import DeficiencyChatSession, StudentTopicScore
    first = assessment(db, "diagnostic", [profile()] * 3, [False, False, True])
    perf.update_topic_scores_from_attempt(first.id)
    topic_id = db.query(StudentTopicScore).one().topic_id
    monkeypatch.setattr(chat, "get_weak_topics", lambda sid: [dict(topic_id=topic_id, topic_name="Fractions", score_percent=100 / 3)])
    monkeypatch.setattr(chat, "_resolve_all_target_threads", lambda sid: (["target"], 9, first.assessment_id))
    monkeypatch.setattr("app.services.lms.class_service.get_student_grade", lambda sid: "9")
    monkeypatch.setattr("app.services.lms.target_question_service.ensure_target_questions", lambda *a: None)
    monkeypatch.setattr("app.services.quiz.mcq_answer_check.verify_mcq_keys", lambda items: [dict(q, key_verified=True) for q in items])
    for n in range(7):
        store_question(db, n + 500, source_pdf_thread_id="target", created_by=9)
    state = chat.start_session(1)
    assert state["total_questions"] == 7
    assert state["current_question"]["source"] == "target_pdf"
    assert "correct_option_index" not in state["current_question"]
    saved = db.get(DeficiencyChatSession, state["session_id"])
    assert all(q["domain"] == "Numbers" for q in json.loads(saved.questions_json))

    latest = assessment(db, "quiz", [profile(cognitive="Knowledge")], [False], when=2)
    monkeypatch.setattr("app.services.lms.learning_path_service.refresh_learning_path", lambda sid: None)
    monkeypatch.setattr(perf, "create_snapshot", lambda sid: None)
    _run_post_submit_steps(latest, get_assessment(latest.assessment_id))
    assert saved.status == "completed"
    assert chat.get_active_session(1) is None
    weak = chat._practice_weak_areas(1, first.assessment_id)
    assert weak[0]["attempt_id"] == latest.id
    assert weak[0]["wrong_metadata"][0]["cognitive_level"] == "Knowledge"


def test_printed_metadata_overrides_ai_fields_without_calling_ai(monkeypatch):
    from app.services.quiz.question_metadata import complete_metadata
    monkeypatch.setattr("app.utils.llm_factory.get_chat_model", lambda **k: pytest.fail("No generation needed"))
    question = mcq(**profile(domain="Algebra", difficulty="easy"))
    source = "Q1. What is 2 + 3?\nDomain: Numbers | Topic: Addition | Cognitive Level: Knowledge | Intended difficulty: Intermediate\n"
    complete_metadata([question], [1], source)
    assert question.domain == "Numbers" and question.topic == "Addition"
    assert question.cognitive_level == "Knowledge" and question.difficulty == "medium"


def test_cognitive_section_headings_apply_to_each_question():
    from app.services.quiz.question_metadata import printed_metadata
    metadata = printed_metadata("PART I: KNOWLEDGE LEVEL (10%)\n1. Example\n2. Example\nSection B: Understanding Level\nQ3. Example\n")
    assert metadata == {1: {"cognitive_level": "Knowledge"}, 2: {"cognitive_level": "Knowledge"},
                        3: {"cognitive_level": "Understanding"}}


def test_rejected_target_keys_use_other_bank_questions_before_generation(db, monkeypatch):
    from app.services.lms.adaptive_practice_service import build_queue
    monkeypatch.setattr("app.services.lms.target_question_service.ensure_target_questions", lambda *a: None)
    bad = store_question(db, 800, source_pdf_thread_id="target", created_by=9)
    good = store_question(db, 801, source_pdf_thread_id="target", created_by=9)
    monkeypatch.setattr("app.services.quiz.mcq_answer_check.verify_mcq_keys", lambda items: [q for q in items if q["question_id"] != bad.id])
    monkeypatch.setattr("app.services.quiz.remediation_generator.generate_remediation_mcqs", lambda **k: pytest.fail("Bank alternative exists"))
    queue = build_queue(dict(topic_id=1, topic_name="Fractions", score_percent=0, wrong_metadata=[profile()]), ["target"], 9, 1)
    assert queue[0]["question_id"] == good.id


def test_target_index_keeps_invalid_extraction_inactive_and_is_idempotent(db, monkeypatch):
    from app.services.lms import target_question_service as target
    from app.models.lms_models import Question
    valid = mcq(**profile())
    invalid = valid.model_copy(update={"question_text": "Unclear source question",
                                       "options": [o.model_copy(update={"text": "4"}) for o in valid.options]})
    monkeypatch.setattr(target, "extract_target_questions", lambda *a: [(valid, 1), (invalid, 2)])
    rows = target.index_target_questions("target", 9, "source")
    assert len(rows) == 2
    assert rows[0].is_active and not rows[1].is_active
    assert rows[1].extraction_confidence == 0
    assert all(q.domain and q.topic_label and q.cognitive_level for q in rows)
    monkeypatch.setattr(target, "extract_target_questions", lambda *a: pytest.fail("Already indexed"))
    assert len(target.index_target_questions("target", 9, "source")) == 2
    assert db.query(Question).count() == 2


def test_plain_study_pdf_does_not_trigger_question_generation(monkeypatch):
    from app.services.lms.target_question_service import extract_target_questions
    monkeypatch.setattr("app.services.quiz.pdf_extractor._llm_extract_qa", lambda *a: pytest.fail("No MCQs to extract"))
    assert extract_target_questions("Unit 1: Numbers\n1. Definitions\n2. Examples\n3. Summary") == []
