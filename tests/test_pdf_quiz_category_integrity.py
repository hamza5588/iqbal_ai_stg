import json
import types

import pytest

from app.services.lms.assessment_service import pdf_category_integrity_errors
from app.services.lms.exceptions import LMSValidationError
from app.services.quiz.concept_labeler import is_generic_concept
from app.services.quiz.models import MCQOption, MCQQuestion
from app.services.quiz.pipeline import (
    _apply_source_domain_mapping,
    _category_metadata,
    _question_categories,
    _source_declares_question_domains,
)


def _pipeline_question(domain=None, concept=None):
    return MCQQuestion(
        question_text="Which option is correct?",
        options=[MCQOption(label=label, text=text) for label, text in zip("ABCD", ["1", "2", "3", "4"])],
        correct_option_label="A",
        domain=domain,
        learning_concept=concept or domain,
    )


@pytest.mark.parametrize(
    "label",
    [
        "Math",
        "Maths",
        "Mathematics",
        "General Mathematics",
        "Mathematics IX",
        "Mathematics Grade IX",
        "Grade 9 Mathematics",
        "DIL SAATHI Weekly Practice Quiz 1 - Mathematics IX Finalized",
    ],
)
def test_subject_wide_math_labels_are_generic(label):
    assert is_generic_concept(label)


def test_real_domains_are_not_generic():
    assert not is_generic_concept("Numbers")
    assert not is_generic_concept("Algebra")


def test_partial_printed_domains_fail_closed():
    questions = [
        _pipeline_question("Numbers" if index < 5 else None)
        for index in range(10)
    ]
    with pytest.raises(LMSValidationError, match="question categories could not be verified"):
        _question_categories(questions, list(range(1, 11)))


def test_source_domain_table_cannot_fall_back_when_extractor_loses_every_domain():
    questions = [_pipeline_question(concept="Fractions") for _ in range(10)]
    with pytest.raises(LMSValidationError, match="question categories could not be verified"):
        _question_categories(
            questions,
            list(range(1, 11)),
            domain_mapping_expected=True,
        )
    assert _source_declares_question_domains(
        "Answer Key\nQ Answer Domain Topic Cognitive Level"
    )


def test_complete_mixed_domains_are_kept_question_by_question():
    domains = ["Numbers"] * 6 + ["Algebra"] + ["Numbers"] * 2 + ["Algebra"]
    questions, labels, explicit = _question_categories(
        [_pipeline_question(domain) for domain in domains],
        list(range(1, 11)),
    )
    assert explicit is True
    assert labels == domains
    assert [question.learning_concept for question in questions] == domains


def test_deterministic_answer_key_domains_override_category_context_loss():
    text = (
        "Answer Key\nQ Answer Domain Topic Cognitive Level\n"
        "1 A Numbers Common Logarithms Knowledge\n"
        "2 B Algebra Factorization Application\n"
    )
    recovered = _apply_source_domain_mapping(
        [_pipeline_question(concept="Fractions"), _pipeline_question(concept="Fractions")],
        [1, 2],
        text,
    )
    assert [question.domain for question in recovered] == ["Numbers", "Algebra"]
    assert [question.learning_concept for question in recovered] == ["Numbers", "Algebra"]


def test_markdown_answer_key_domains_map_by_source_number_not_list_position():
    text = (
        "| Q | Answer | Domain | Topic | Cognitive Level |\n"
        "|---|---|---|---|---|\n"
        "| 7 | A | Algebra | Linear Equations | Application |\n"
        "| 10 | C | Numbers | Common Logarithms | Knowledge |\n"
    )
    recovered = _apply_source_domain_mapping(
        [_pipeline_question(concept="Wrong"), _pipeline_question(concept="Wrong")],
        [10, 7],
        text,
    )
    assert [question.domain for question in recovered] == ["Numbers", "Algebra"]
    assert [question.learning_concept for question in recovered] == ["Numbers", "Algebra"]


def test_reprocessing_replaces_stale_category_maps():
    old = json.dumps(
        {
            "keep_me": "yes",
            "question_concepts": {"1": "Mathematics"},
            "question_pdf_topics": {"1": "Mathematics"},
            "concept_source": "pdf_domain",
        }
    )
    saved = json.loads(
        _category_metadata(
            old,
            {"10": "Numbers", "11": "Algebra"},
            {"10": "Numbers", "11": "Algebra"},
            categories_are_pdf_domains=True,
            is_diagnostic=False,
        )
    )
    assert saved["keep_me"] == "yes"
    assert saved["question_concepts"] == {"10": "Numbers", "11": "Algebra"}
    assert saved["question_pdf_topics"] == saved["question_concepts"]
    assert saved["concept_source"] == "pdf_domain"


def test_answer_key_enrichment_does_not_stop_when_domains_are_partial(monkeypatch):
    from app.services.quiz import hybrid_vision_pipeline as hv

    questions = []
    for number in range(1, 11):
        questions.append(
            hv.MCQ(
                question_no=number,
                question=f"Question {number}",
                options=[hv.MCQOption(label=label, text=text) for label, text in zip("ABCD", ["1", "2", "3", "4"])],
                correct_answer="A",
                domain="Numbers" if number <= 5 else None,
                topic=f"Topic {number}" if number <= 5 else None,
            )
        )
    result = hv.QuizExtraction(title="Quiz", total_questions=10, questions=questions)
    text_evidence = {
        "pages": [
            {
                "page_number": 1,
                "plain_text": (
                    "Answer Key\nQ Answer Domain Topic Cognitive Level\n"
                    + "\n".join(f"{number} A Numbers Topic{number} Knowledge" for number in range(1, 6))
                ),
            }
        ]
    }
    complete = hv.DocumentEnrichment(
        title="Quiz",
        entries=[
            hv.AnswerKeyEntry(
                question_no=number,
                correct_answer="A",
                domain="Algebra" if number in (7, 10) else "Numbers",
                topic=f"Topic {number}",
            )
            for number in range(1, 11)
        ],
    )
    answers_only = hv.DocumentEnrichment(
        title="Quiz",
        entries=[
            hv.AnswerKeyEntry(question_no=number, correct_answer="A")
            for number in range(1, 11)
        ],
    )
    calls = []

    class _Structured:
        def invoke(self, messages):
            calls.append(messages)
            return types.SimpleNamespace(content="{}")

    monkeypatch.setattr(hv, "create_vision_llm", lambda: object())
    monkeypatch.setattr(hv, "create_structured_llm", lambda *_args, **_kwargs: _Structured())
    monkeypatch.setattr(hv, "parse_answer_key_from_text", lambda _evidence: answers_only)
    monkeypatch.setattr(hv, "parse_json_model", lambda *_args, **_kwargs: complete)

    enriched = hv.enrich_with_answer_key(result, text_evidence, [])
    assert calls, "partial domains must trigger the enrichment fallback"
    assert [question.domain for question in enriched.questions] == [
        "Numbers",
        "Numbers",
        "Numbers",
        "Numbers",
        "Numbers",
        "Numbers",
        "Algebra",
        "Numbers",
        "Numbers",
        "Algebra",
    ]


def test_hybrid_validation_rejects_partial_domain_mapping():
    from app.services.quiz import hybrid_vision_pipeline as hv

    questions = [
        hv.MCQ(
            question_no=number,
            question=f"Question {number}",
            options=[hv.MCQOption(label=label, text=text) for label, text in zip("ABCD", ["1", "2", "3", "4"])],
            correct_answer="A",
            domain="Numbers" if number <= 5 else None,
        )
        for number in range(1, 11)
    ]
    passed, messages = hv.validate_result(
        hv.QuizExtraction(title="Quiz", total_questions=10, questions=questions),
        {"pages": [{"page_number": 1, "plain_text": "Answer Key Domain Topic"}]},
    )
    assert passed is False
    assert any("Domain mapping is incomplete" in message for message in messages)


def _assessment(concepts, pdf_topics, topic_names, *, source="pdf_domain"):
    rows = []
    for index, topic_name in enumerate(topic_names, start=1):
        question = types.SimpleNamespace(
            id=index,
            source_question_number=index,
            topic_id=index if topic_name else None,
            topic=types.SimpleNamespace(name=topic_name) if topic_name else None,
        )
        rows.append(types.SimpleNamespace(question_id=index, sort_order=index - 1, question=question))
    return types.SimpleNamespace(
        creation_mode="pdf_qa_auto",
        description=json.dumps(
            {
                "question_concepts": concepts,
                "question_pdf_topics": pdf_topics,
                "concept_source": source,
            }
        ),
        questions=rows,
    )


def test_publish_integrity_accepts_complete_matching_domains():
    concepts = {"1": "Numbers", "2": "Algebra"}
    assert pdf_category_integrity_errors(
        _assessment(concepts, dict(concepts), ["Numbers", "Algebra"])
    ) == []


def test_publish_integrity_reports_missing_generic_and_stale_data():
    errors = pdf_category_integrity_errors(
        _assessment(
            {"1": "Mathematics", "99": "Numbers"},
            {"1": "Mathematics", "99": "Numbers"},
            ["Mathematics", None],
        )
    )
    joined = " | ".join(errors)
    assert "stale question_concepts IDs 99" in joined
    assert "Q1 has generic category" in joined
    assert "Q2 missing question_concepts category" in joined
    assert "Q2 missing topic_id" in joined


def test_pdf_question_resolution_never_uses_assessment_title(monkeypatch):
    from app.services.lms import performance_service as performance

    monkeypatch.setattr(performance, "resolve_topic_id_from_text", lambda _text: None)
    question = types.SimpleNamespace(
        id=1,
        topic_id=None,
        question_text="Which option is correct?",
        source_pdf_thread_id=None,
    )
    assessment = types.SimpleNamespace(
        description="{}",
        creation_mode="pdf_qa_auto",
        title="DIL SAATHI Weekly Practice Quiz 1 - Mathematics IX Finalized",
    )
    assert performance._resolve_question_topic_id(question, assessment) is None
