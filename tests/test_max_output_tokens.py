"""
Tests for bug #27: the legacy ChatModel (app/models/models.py) and ChatbotService
(app/services/chatbot_service.py) hardcoded max_tokens=1024 when building their LLM via
create_llm(), silently bypassing Config.OPENAI_MAX_TOKENS/GROQ_MAX_TOKENS and capping
replies to 1024 tokens regardless of the configured/real model capacity - unlike every
other create_llm() caller in the app (RAG chat passes its own RAG_RESPONSE_MAX_TOKENS
and clamps to the model's real limit; TeacherLessonService passes no cap at all).

Fix: both call sites no longer pass a hardcoded max_tokens, so they fall into the same
"no artificial cap outside load-test mode" behavior create_llm() already implements for
every other non-RAG-chat caller - config values are honored consistently instead of one
magic number silently overriding them.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import pytest

llm_factory = pytest.importorskip("app.utils.llm_factory")


class _FakeChatOpenAI:
    """Captures the kwargs create_llm would have passed to ChatOpenAI."""

    last_kwargs = None

    def __init__(self, **kwargs):
        _FakeChatOpenAI.last_kwargs = kwargs


@pytest.fixture(autouse=True)
def _patch_chat_openai(monkeypatch):
    monkeypatch.setattr(llm_factory, "ChatOpenAI", _FakeChatOpenAI)
    _FakeChatOpenAI.last_kwargs = None
    yield


def test_no_explicit_cap_outside_load_test_mode_sends_no_max_tokens(monkeypatch):
    """
    Mirrors what ChatModel/ChatbotService now do: call create_llm() with no max_tokens.
    Outside load-test mode, no artificial cap should be sent to the provider - the model's
    own maximum governs, instead of a hardcoded/forgotten small number cutting replies off.
    """
    monkeypatch.delenv("LOAD_TEST_MODE", raising=False)

    llm_factory.create_llm(temperature=0.5, api_key="sk-test", provider="openai")

    assert "max_tokens" not in _FakeChatOpenAI.last_kwargs


def test_load_test_mode_still_applies_the_configured_cap(monkeypatch):
    """The load-test cap (the actual designed purpose of *_MAX_TOKENS) must still work."""
    monkeypatch.setenv("LOAD_TEST_MODE", "true")

    llm_factory.create_llm(temperature=0.5, api_key="sk-test", provider="openai")

    assert "max_tokens" in _FakeChatOpenAI.last_kwargs
    assert _FakeChatOpenAI.last_kwargs["max_tokens"] is not None


def test_explicit_caller_override_is_still_respected(monkeypatch):
    """A caller that DOES want a cap (e.g. RAG chat's RAG_RESPONSE_MAX_TOKENS) still works."""
    monkeypatch.delenv("LOAD_TEST_MODE", raising=False)

    llm_factory.create_llm(temperature=0.5, max_tokens=4096, api_key="sk-test", provider="openai")

    assert _FakeChatOpenAI.last_kwargs["max_tokens"] == 4096


def test_chat_model_no_longer_hardcodes_1024():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "app" / "models" / "models.py").read_text(
        encoding="utf-8"
    )
    assert "max_tokens=1024" not in src


def test_chatbot_service_no_longer_hardcodes_1024():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent / "app" / "services" / "chatbot_service.py"
    ).read_text(encoding="utf-8")
    assert "max_tokens=1024" not in src
