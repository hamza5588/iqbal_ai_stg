"""Answer-aware, non-progressive Learning Chat tutor regressions."""

import json

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.lms_models import Base, DeficiencyChatSession


def _question():
    return {
        "topic_id": 5,
        "topic_name": "Linear equations",
        "question_text": "Which value makes x + 2 = 5?",
        "options": [
            {"label": "A", "text": "3"},
            {"label": "B", "text": "7"},
        ],
        "correct_option_index": 0,
        "difficulty": "easy",
        "source": "target_pdf",
        "rag_thread_id": "thread-1",
        "generation_policy": "pdf_slo_only_v2",
        "approved_slo": "Solve linear equations",
        "grounding_verified": "source_scope_v1",
        "answered": False,
        "correct": None,
    }


@pytest.fixture()
def tutor_env(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    import app.utils.db as db_mod
    from app.services.lms import class_service
    from app.services.lms import deficiency_chat_service as service

    monkeypatch.setattr(db_mod, "get_session_factory", lambda: (lambda: db))
    monkeypatch.setattr(service, "_resolve_all_target_threads", lambda _sid: (["thread-1"], 99, 44))
    monkeypatch.setattr(service, "_refresh_question_display", lambda question: question)
    monkeypatch.setattr(service, "get_section_text", lambda *_a, **_k: "Target PDF explanation")
    monkeypatch.setattr(class_service, "get_student_grade", lambda _sid: "9")

    app = Flask(__name__)
    with app.app_context():
        row = DeficiencyChatSession(
            student_id=1,
            diagnostic_assessment_id=44,
            rag_thread_id="thread-1",
            rag_owner_id=99,
            weak_topics_json="[]",
            questions_json=json.dumps([_question()]),
            current_index=0,
            correct_count=0,
            chat_history_json="{}",
            status="active",
        )
        db.add(row)
        db.commit()
        yield db, service, row
    db.close()


def test_wrong_selection_reaches_tutor_without_static_progression(tutor_env, monkeypatch):
    db, service, row = tutor_env
    answer_state = service.submit_answer(row.id, 1, 1)
    assert answer_state["last_answer"]["correct"] is False
    assert answer_state["last_answer"]["solved"] is False
    assert answer_state["last_answer"]["stay_on_question"] is True
    assert "last_selected_option_index" not in answer_state["current_question"]
    assert answer_state["current_question"].get("solved_review") is False

    captured = []

    def fake_tutor_chat(message, **kwargs):
        captured.append({"message": message, **kwargs})
        return "What made option B seem right to you?"

    monkeypatch.setattr(service.tutor_service, "tutor_chat", fake_tutor_chat)
    first = service.explain_with_tutor(row.id, 1, "Why was my choice wrong?")
    second = service.explain_with_tutor(row.id, 1, "I added the values.")

    assert first == {"reply": "What made option B seem right to you?", "session_id": row.id}
    assert second == first
    assert "Student's latest selected option (incorrect): B: 7" in captured[0]["context"]
    assert "SOLVED: false" in captured[0]["context"]
    assert "Correct option (use in Mode B solution)" not in captured[0]["context"]
    assert captured[0].get("solved") is False
    assert captured[1]["history"][-1] == {
        "role": "assistant",
        "content": "What made option B seem right to you?",
    }

    db.refresh(row)
    tutor_state = json.loads(row.chat_history_json)
    assert "assist_level" not in tutor_state
    assert len(tutor_state["messages"]) == 4


def test_correct_answer_stays_for_mode_b_then_advance_completes(tutor_env, monkeypatch):
    db, service, row = tutor_env
    answer_state = service.submit_answer(row.id, 1, 0)
    assert answer_state["last_answer"]["correct"] is True
    assert answer_state["last_answer"]["solved"] is True
    assert answer_state["last_answer"]["stay_on_question"] is True
    assert answer_state["last_answer"]["explanation_available"] is True
    assert answer_state["current_index"] == 0
    assert answer_state["current_question"]["solved_review"] is True
    assert answer_state["completed"] is False
    assert answer_state["correct_count"] == 1

    captured = []

    def fake_tutor_chat(message, **kwargs):
        captured.append({"message": message, **kwargs})
        return "Nice work — A is right. Step 1: undo +2. Step 2: x = 3."

    monkeypatch.setattr(service.tutor_service, "tutor_chat", fake_tutor_chat)
    explained = service.explain_with_tutor(row.id, 1, "Why is my answer correct?")
    assert "A is right" in explained["reply"]
    assert captured[0].get("solved") is True
    assert "SOLVED: true" in captured[0]["context"]
    assert "Correct option (use in Mode B solution): A: 3." in captured[0]["context"]

    advanced = service.advance_session(row.id, 1)
    assert advanced["completed"] is True
    assert advanced.get("advanced_from_solved") is True
    db.refresh(row)
    assert row.current_index == 1
    assert row.status == "completed"


def test_mode_b_does_not_block_naming_correct_option(tutor_env, monkeypatch):
    _, service, row = tutor_env
    service.submit_answer(row.id, 1, 0)
    monkeypatch.setattr(
        service.tutor_service,
        "tutor_chat",
        lambda *_a, **_k: "Yes — A is right. x + 2 = 5 so x = 3.",
    )
    result = service.explain_with_tutor(row.id, 1, "Why is my answer correct?")
    assert "A is right" in result["reply"]
    assert result["reply"] != service._ANSWER_GUARD_REPLY


def test_explicit_current_answer_leak_is_blocked(tutor_env, monkeypatch):
    _, service, row = tutor_env
    service.submit_answer(row.id, 1, 1)
    monkeypatch.setattr(
        service.tutor_service,
        "tutor_chat",
        lambda *_a, **_k: "The correct answer is option A because x equals 3.",
    )

    result = service.explain_with_tutor(row.id, 1, "Just tell me the answer")
    assert result["reply"] == service._ANSWER_GUARD_REPLY
    assert "option A" not in result["reply"]


def test_answer_leak_is_rewritten_by_model_before_static_fallback(tutor_env, monkeypatch):
    _, service, row = tutor_env
    service.submit_answer(row.id, 1, 1)
    replies = iter([
        "The correct answer is option A.",
        "Let's undo the +2 together: what is 5 take away 2?",
    ])
    contexts = []

    def fake_tutor_chat(message, **kwargs):
        contexts.append(kwargs["context"])
        return next(replies)

    monkeypatch.setattr(service.tutor_service, "tutor_chat", fake_tutor_chat)
    result = service.explain_with_tutor(row.id, 1, "Just tell me the answer")
    assert result["reply"] == "Let's undo the +2 together: what is 5 take away 2?"
    assert "previous draft named the correct option" in contexts[1]


def test_answer_as_a_verb_does_not_trigger_option_a_guard(tutor_env):
    _, service, _ = tutor_env
    assert service._reveals_current_mcq_answer(
        "Can you answer a smaller question first?",
        _question(),
    ) is False
