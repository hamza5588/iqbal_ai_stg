"""Regression: Groq tool_use_failed with worded confidence must be recoverable."""

from __future__ import annotations

from app.services.quiz.models import MCQBatchResult, MCQQuestion
from app.services.quiz.retry_utils import (
    _repair_jsonish_literals,
    parse_groq_failed_generation,
)


def test_repair_zero_dot_nine_word():
    raw = '{"quiz_title":"T","questions":[],"conversion_confidence": 0. nine}'
    # field may be nested; repair the literal form used in tool args
    fixed = _repair_jsonish_literals(
        '{"conversion_confidence": 0. nine, "confidence": 0.eight}'
    )
    assert '"conversion_confidence": 0.9' in fixed.replace(" ", "") or (
        '"conversion_confidence":0.9' in fixed.replace(" ", "")
    )
    assert "0.8" in fixed


def test_parse_failed_generation_mcq_batch_with_word_confidence():
    failed = r"""MCQBatchResult({
  "quiz_title": "Diagnostic Quiz",
  "questions": [
    {
      "question_text": "Which of the following numbers is irrational?",
      "question_latex": null,
      "options": [
        {"label": "A", "text": "0.101001...", "latex": null},
        {"label": "B", "text": "sqrt 12", "latex": "\\sqrt{12}"},
        {"label": "C", "text": "3/7", "latex": null},
        {"label": "D", "text": "0.333...", "latex": null}
      ],
      "correct_option_label": "B",
      "explanation": null,
      "learning_concept": "Irrational numbers",
      "conversion_confidence": 0. nine,
      "preserve_option_order": false
    }
  ],
  "failed_conversions": []
})"""
    exc = Exception(
        "Error code: 400 - {'error': {'message': 'Failed to parse tool call arguments as JSON', "
        "'type': 'invalid_request_error', 'code': 'tool_use_failed', "
        f"'failed_generation': {failed!r}}}"
    )
    # Attach body like Groq SDK often does
    body = {
        "error": {
            "message": "Failed to parse tool call arguments as JSON",
            "type": "invalid_request_error",
            "code": "tool_use_failed",
            "failed_generation": failed,
        }
    }
    exc.body = body  # type: ignore[attr-defined]

    recovered = parse_groq_failed_generation(exc, MCQBatchResult)
    assert recovered is not None
    assert len(recovered.questions) == 1
    assert recovered.questions[0].correct_option_label == "B"
    assert abs(recovered.questions[0].conversion_confidence - 0.9) < 1e-6


def test_parse_equals_style_quiz_extraction_failed_generation():
    """Groq json_validate_failed sometimes returns {key=value} with stem= aliases."""
    from app.services.quiz.hybrid_vision_pipeline import QuizExtraction
    from app.services.quiz.retry_utils import (
        _coerce_failed_generation_data,
        _normalize_structured_payload,
        parse_groq_failed_generation,
    )

    failed = (
        "{requested_page_count=3, total_questions=2, questions=["
        "{question_no=1, stem=According to identities, $(a+b)^2$ is:, "
        "options=[{label=A, text=$2ab$}, {label=B, text=$4ab$}, "
        "{label=C, text=$2(a^2+b^2)$}, {label=D, text=$a^2-b^2$}], "
        "domain=Algebra, topic=Algebraic Identities, "
        "cognitive_level=Knowledge, difficulty=Intermediate, correct_answer=C}, "
        "{question_no=6, stem=Find x if $\\log_2 x=5$:, "
        "options=[{label=A, text=10}, {label=B, text=25}, "
        "{label=C, text=32}, {label=D, text=64}], "
        "domain=Numbers, topic=Exponential and Logarithmic Forms, "
        "cognitive_level=Application, difficulty=Intermediate, correct_answer=C}], "
        "title=Weekly Practice Quiz 2}"
    )
    data = _coerce_failed_generation_data(failed)
    assert isinstance(data, dict)
    assert data.get("total_questions") == 2
    assert data["questions"][0]["stem"].startswith("According")
    assert data["questions"][1]["options"][2]["text"] == "32"

    normalized = _normalize_structured_payload(data, QuizExtraction)
    assert "requested_page_count" not in normalized
    assert normalized["questions"][0]["question"].startswith("According")
    assert "stem" not in normalized["questions"][0]

    exc = Exception(
        "Error code: 400 - {'error': {'message': \"Failed to generate JSON.\", "
        "'type': 'invalid_request_error', 'code': 'json_validate_failed', "
        f"'failed_generation': {failed!r}}}"
    )
    exc.body = {  # type: ignore[attr-defined]
        "error": {
            "message": "Failed to generate JSON.",
            "code": "json_validate_failed",
            "failed_generation": failed,
        }
    }
    recovered = parse_groq_failed_generation(exc, QuizExtraction)
    assert recovered is not None
    assert recovered.total_questions == 2
    assert recovered.questions[0].correct_answer == "C"
    assert recovered.questions[1].question_no == 6
    assert recovered.questions[1].correct_answer == "C"


def test_mcq_confidence_field_coerces_wordy_strings():
    q = MCQQuestion(
        question_text="Q",
        options=[
            {"label": "A", "text": "1"},
            {"label": "B", "text": "2"},
            {"label": "C", "text": "3"},
            {"label": "D", "text": "4"},
        ],
        correct_option_label="A",
        conversion_confidence="0. nine",  # type: ignore[arg-type]
    )
    assert abs(q.conversion_confidence - 0.9) < 1e-6
