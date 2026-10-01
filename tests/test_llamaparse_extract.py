"""Unit tests for LlamaParse quiz/diagnostic PDF extract wiring."""

from __future__ import annotations

import os

import pytest

from app.services.quiz.llamaparse_extract import (
    extract_pdf_text_llamaparse,
    llamaparse_enabled,
)


def test_llamaparse_disabled_without_key(monkeypatch):
    monkeypatch.delenv("LLAMA_CLOUD_API_KEY", raising=False)
    monkeypatch.setenv("QUIZ_LLAMAPARSE", "1")
    assert llamaparse_enabled() is False


def test_llamaparse_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "test-key")
    monkeypatch.setenv("QUIZ_LLAMAPARSE", "0")
    assert llamaparse_enabled() is False


def test_extract_writes_temp_and_joins_docs(monkeypatch, tmp_path):
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "test-key")

    class _Doc:
        def __init__(self, text):
            self.text = text

    class _FakeParser:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def load_data(self, path):
            assert os.path.isfile(path)
            return [_Doc("Hello Q1"), _Doc("Answer Key")]

    monkeypatch.setattr(
        "llama_parse.LlamaParse",
        _FakeParser,
        raising=False,
    )
    # Ensure import path used inside extract resolves
    import sys
    import types

    fake_mod = types.ModuleType("llama_parse")
    fake_mod.LlamaParse = _FakeParser
    monkeypatch.setitem(sys.modules, "llama_parse", fake_mod)

    out = extract_pdf_text_llamaparse(b"%PDF-1.4 fake", filename="exam.pdf")
    assert "Hello Q1" in out
    assert "Answer Key" in out


def test_try_llamaparse_used_by_task_helper(monkeypatch):
    from app.tasks import quiz_pdf_tasks as tasks

    monkeypatch.setattr(
        "app.services.quiz.llamaparse_extract.llamaparse_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.quiz.llamaparse_extract.extract_pdf_text_llamaparse",
        lambda *_a, **_k: "Parsed markdown from LlamaParse",
    )
    text = tasks._try_llamaparse_text(b"%PDF", "a.pdf")
    assert text == "Parsed markdown from LlamaParse"
