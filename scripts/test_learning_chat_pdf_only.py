"""Regression checks for target-PDF-only Learning Chat practice."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.lms_models import Base, DeficiencyChatSession
from app.services.lms import deficiency_chat_service as chat
from app.services.lms.exceptions import LMSValidationError
from app.services.quiz import diagnostic_generator as pdf_gen
from app.services.quiz.models import MCQBatchResult


def test_no_matching_target_pdf_text_never_calls_general_ai(monkeypatch):
    monkeypatch.setattr(chat, "_match_target_section", lambda name, threads: (name, "t1", 0))
    monkeypatch.setattr(chat, "get_section_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        chat, "generate_mcqs_from_content",
        lambda *args, **kwargs: pytest.fail("No PDF text should mean no AI generation"),
    )
    queue = chat._build_question_queue(
        [{"topic_id": 1, "topic_name": "Fractions", "score_percent": 50}],
        ["t1"], 9, "9",
    )
    assert queue == []


def test_strict_pdf_section_does_not_return_unrelated_first_page(monkeypatch):
    monkeypatch.setattr(
        pdf_gen, "query_all_chunks",
        lambda **kwargs: [{"page": 1, "chunk_index": 1, "text": "Algebra practice"}],
    )
    assert pdf_gen.get_section_text("t1", 9, "Geometry", require_topic_match=True) == ""
    assert pdf_gen.get_section_text("t1", 9, "Geometry") == "Algebra practice"


def test_strict_pdf_section_includes_explanation_next_to_heading(monkeypatch):
    monkeypatch.setattr(
        pdf_gen, "query_all_chunks",
        lambda **kwargs: [
            {"page": 1, "chunk_index": 0, "text": "Geometry unrelated content"},
            {"page": 3, "chunk_index": 0, "text": "Fractions"},
            {"page": 3, "chunk_index": 1, "text": "A proper fraction has a smaller numerator than denominator."},
        ],
    )
    text = pdf_gen.get_section_text("t1", 9, "Fractions", require_topic_match=True)
    assert "A proper fraction" in text
    assert "Geometry" not in text


def test_strict_generator_accepts_empty_source_supported_batch(monkeypatch):
    prompts = []
    monkeypatch.setattr(pdf_gen, "get_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(pdf_gen, "invoke_with_groq_rate_limit", lambda fn, **kwargs: fn())

    def invoke(_model, _schema, prompt):
        prompts.append(prompt)
        return MCQBatchResult(questions=[])

    monkeypatch.setattr(pdf_gen, "invoke_structured", invoke)
    assert pdf_gen.generate_mcqs_from_content(
        "A short definition only.", "Fractions", 5, strict_grounding=True
    ) == []
    assert len(prompts) == 1
    assert "Return fewer questions, or an empty questions list" in prompts[0]
    assert "Do not fill gaps from general topic knowledge" in prompts[0]


@pytest.fixture()
def legacy_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DeficiencyChatSession.__table__])
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(chat, "get_db", lambda: db)
    monkeypatch.setattr(chat, "_resolve_all_target_threads", lambda student_id: (["t1"], 9, 5))
    monkeypatch.setattr(chat, "_refresh_question_display", lambda q: q)
    yield db
    db.close()
    engine.dispose()


def _question(text, source, policy=None):
    return {
        "topic_id": 1, "topic_name": "Fractions", "question_text": text,
        "options": [{"label": "A", "text": "1"}, {"label": "B", "text": "2"}],
        "correct_option_index": 0, "source": source, "rag_thread_id": "t1",
        "generation_policy": policy, "key_verified": "llm", "difficulty": "easy",
        "answered": False, "correct": None,
    }


def test_legacy_fallback_and_old_pdf_queue_cannot_be_served(legacy_session):
    fresh = _question("New PDF question", "target_pdf", "pdf_only_v1")
    row = DeficiencyChatSession(
        student_id=4, rag_thread_id="t1", rag_owner_id=9,
        questions_json=json.dumps([
            _question("General AI question", "grade_fit_ai"),
            _question("Old PDF prompt question", "target_pdf"),
            fresh,
        ]),
        weak_topics_json="[]", status="active",
    )
    legacy_session.add(row)
    legacy_session.commit()

    state = chat.get_session(row.id, 4)
    assert state["current_question"]["question_text"] == "New PDF question"
    assert state["has_pdf"] is True
    assert [q["question_text"] for q in json.loads(row.questions_json)] == ["New PDF question"]


def test_legacy_queue_without_pdf_preserves_answered_history_without_mastery(legacy_session, monkeypatch):
    calls = []
    monkeypatch.setattr(chat, "_mark_learning_path_chat_complete", lambda student_id: calls.append(student_id))
    history = _question("Answered PDF question", "target_pdf", "pdf_only_v1")
    history.update(answered=True, correct=True)
    row = DeficiencyChatSession(
        student_id=4, rag_thread_id="t1", rag_owner_id=9,
        questions_json=json.dumps([history, _question("General AI question", "grade_fit_ai")]),
        current_index=1, correct_count=1, weak_topics_json="[]", status="active",
    )
    legacy_session.add(row)
    legacy_session.commit()

    with pytest.raises(LMSValidationError, match="target PDF"):
        chat.get_session(row.id, 4)
    assert row.status == "completed" and row.current_index == 1 and row.correct_count == 1
    assert json.loads(row.questions_json) == [history]
    assert calls == []
    assert chat.get_session(row.id, 4)["current_question"] is None
