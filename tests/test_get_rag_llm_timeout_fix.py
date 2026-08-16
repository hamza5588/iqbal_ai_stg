"""
Regression test: get_rag_llm(user_id=..., timeout=..., temperature=...) used to raise
"get_chat_model() got multiple values for keyword argument 'timeout'" because it called
get_chat_model(user_id=user_id, timeout=120, temperature=0.5, **kwargs) while kwargs already
contained the caller's own timeout/temperature (e.g. get_rag_llm(user_id=X, timeout=8,
temperature=0), used by several call sites in rag_service.py). The exception was silently
caught and fell back to the Admin Panel key/model on every such call instead of the caller's
actual settings - discovered live via repeated "falling back to Admin Panel key" warnings in
staging logs during RAG chat requests.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import pytest

rag_service = pytest.importorskip("app.utils.rag_service")


def test_get_rag_llm_does_not_collide_on_caller_supplied_timeout_and_temperature(monkeypatch):
    captured = {}

    def fake_get_chat_model(user_id=None, **kwargs):
        captured["user_id"] = user_id
        captured.update(kwargs)
        return "fake-llm-instance"

    monkeypatch.setattr(rag_service, "get_chat_model", fake_get_chat_model)

    # Mirrors real call sites like rag_service.py:639 (get_rag_llm(user_id=uid, timeout=8, temperature=0))
    result = rag_service.get_rag_llm(user_id=42, timeout=8, temperature=0)

    assert result == "fake-llm-instance"
    assert captured["user_id"] == 42
    # Caller-supplied values must win over get_rag_llm's own defaults, not collide with them.
    assert captured["timeout"] == 8
    assert captured["temperature"] == 0


def test_get_rag_llm_falls_back_to_defaults_when_caller_omits_timeout_and_temperature(monkeypatch):
    captured = {}

    def fake_get_chat_model(user_id=None, **kwargs):
        captured.update(kwargs)
        return "fake-llm-instance"

    monkeypatch.setattr(rag_service, "get_chat_model", fake_get_chat_model)

    rag_service.get_rag_llm(user_id=7)

    assert captured["timeout"] == 120
    assert captured["temperature"] == 0.5
