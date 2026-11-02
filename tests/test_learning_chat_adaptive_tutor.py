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
    assert "last_selected_option_index" not in answer_state["current_question"]

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
    assert "correct_option_index" not in captured[0]["context"]
    assert captured[1]["history"][-1] == {
        "role": "assistant",
        "content": "What made option B seem right to you?",
    }

    db.refresh(row)
    tutor_state = json.loads(row.chat_history_json)
    assert "assist_level" not in tutor_state
    assert len(tutor_state["messages"]) == 4


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


def test_answer_as_a_verb_does_not_trigger_option_a_guard(tutor_env):
    _, service, _ = tutor_env
    assert service._reveals_current_mcq_answer(
        "Can you answer a smaller question first?",
        _question(),
    ) is False
