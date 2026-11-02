import types

from app.services.quiz import mcq_grounding_check as grounding


def _options(*values):
    return [
        {"label": "ABCD"[index], "text": value}
        for index, value in enumerate(values)
    ]


def _question(stem):
    return {
        "question_text": stem,
        "options": _options("one", "two", "three", "four"),
        "correct_option_index": 0,
    }


def _fake_llm(monkeypatch, reply="", error=None):
    calls = []

    class FakeLLM:
        model_name = "openai/gpt-oss-120b"

        def bind(self, **kwargs):
            return self

        def invoke(self, prompt):
            calls.append(prompt)
            if error:
                raise error
            return types.SimpleNamespace(content=reply)

    monkeypatch.setattr(grounding, "get_chat_model", lambda **kwargs: FakeLLM())
    monkeypatch.setattr(
        grounding,
        "invoke_with_groq_rate_limit",
        lambda fn, **kwargs: fn(),
    )
    return calls


def test_parse_grounding_verdicts_is_strict_and_first_wins():
    assert grounding.parse_grounding_verdicts(
        "Q0: SUPPORTED | Rational number: a number that can be written as a/b\n"
        "Q1: UNSUPPORTED\nQ0: UNSUPPORTED"
    ) == {
        0: (True, "Rational number: a number that can be written as a/b"),
        1: (False, ""),
    }


def test_supported_question_requires_verbatim_source_evidence(monkeypatch):
    source = "Rational number: a number that can be written as a/b where b is not 0."
    _fake_llm(
        monkeypatch,
        "Q0: SUPPORTED | Rational number: a number that can be written as a/b",
    )
    kept = grounding.verify_mcq_grounding(
        [_question("Which number is rational?")], source, "Rational Numbers"
    )
    assert len(kept) == 1
    assert kept[0]["approved_slo"] == "Rational Numbers"
    assert kept[0]["grounding_verified"] == grounding.GROUNDING_VERIFIER
    assert "evidence" not in kept[0]


def test_valid_key_out_of_syllabus_question_is_rejected(monkeypatch):
    source = "Irrational numbers have non-terminating and non-recurring decimals."
    _fake_llm(
        monkeypatch,
        "Q0: SUPPORTED | Irrational numbers have non-terminating and non-recurring decimals.\n"
        "Q1: UNSUPPORTED",
    )
    items = [
        _question("Which decimal form describes an irrational number?"),
        _question("What is the derivative of x squared?"),
    ]
    kept = grounding.verify_mcq_grounding(items, source, "Irrational Numbers")
    assert [item["question_text"] for item in kept] == [items[0]["question_text"]]


def test_supported_verdict_with_paraphrased_or_missing_evidence_is_rejected(monkeypatch):
    source = "The logarithm is the exponent."
    _fake_llm(monkeypatch, "Q0: SUPPORTED | A logarithm tells us an exponent")
    assert grounding.verify_mcq_grounding(
        [_question("What does a logarithm represent?")], source, "Logarithms"
    ) == []


def test_grounding_reviewer_failure_is_fail_closed(monkeypatch):
    _fake_llm(monkeypatch, error=RuntimeError("provider unavailable"))
    assert grounding.verify_mcq_grounding(
        [_question("Which number is rational?")],
        "A rational number can be written as a/b.",
        "Rational Numbers",
    ) == []


def test_only_incorrect_question_slos_are_attached(monkeypatch):
    from app.services.lms import deficiency_chat_service as service
    from app.models.lms_models import AttemptAnswer, Question

    questions = [
        types.SimpleNamespace(id=1, explanation="Cognitive level: Knowledge; Topic: Common Logarithms"),
        types.SimpleNamespace(id=2, explanation="Topic: Factorization"),
    ]
    answers = [
        types.SimpleNamespace(question_id=1, is_correct=True),
        types.SimpleNamespace(question_id=2, is_correct=False),
    ]

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args):
            return self

        def all(self):
            return self.rows

    class DB:
        def query(self, model):
            return Query(questions if model is Question else answers if model is AttemptAnswer else [])

    monkeypatch.setattr(service, "get_db", lambda: DB())
    areas = [{"topic_id": 7, "topic_name": "Algebra", "question_ids": [1, 2]}]
    scoped = service._attach_approved_slos(
        areas, types.SimpleNamespace(id=44), incorrect_only=True
    )
    assert scoped[0]["slo_names"] == ["Factorization"]


def test_enrichment_uses_the_same_grounded_pdf_queue(monkeypatch):
    from app.services.lms import deficiency_chat_service as service

    captured = {}

    def build(areas, threads, owner, grade, used_question_texts_by_topic=None):
        captured.update(areas=areas, threads=threads, owner=owner, grade=grade)
        return [{
            "question_text": "Grounded challenge",
            "source": "target_pdf",
            "generation_policy": "pdf_slo_only_v2",
            "approved_slo": "Factorization",
            "grounding_verified": "source_scope_v1",
            "rag_thread_id": "thread-1",
        }]

    monkeypatch.setattr(service, "_build_question_queue", build)
    queue = service._build_enrichment_queue(
        [{"topic_id": 7, "topic_name": "Algebra", "slo_names": ["Factorization"]}],
        ["thread-1"],
        9,
        "9",
    )
    assert captured["threads"] == ["thread-1"]
    assert captured["areas"][0]["min_questions"] == 2
    assert queue[0]["source"] == "target_pdf"
    assert queue[0]["learning_chat_mode"] == "enrichment"


def test_question_budget_is_distributed_across_missed_slos(monkeypatch):
    from app.services.lms import deficiency_chat_service as service

    calls = []

    def build_scope(**kwargs):
        calls.append((kwargs["approved_scope"], list(kwargs["ladder"])))
        return [
            {
                "question_text": f"{kwargs['approved_scope']} {index}",
                "approved_slo": kwargs["approved_scope"],
            }
            for index in range(len(kwargs["ladder"]))
        ]

    monkeypatch.setattr(service, "_build_scope_queue", build_scope)
    queue = service._build_question_queue(
        [{
            "topic_id": 1,
            "topic_name": "Numbers",
            "slo_names": ["Common Logarithms", "Irrational Numbers"],
            "score_percent": 50,
        }],
        ["thread-1"],
        9,
        "9",
    )
    assert len(queue) == 5
    assert [(scope, len(ladder)) for scope, ladder in calls] == [
        ("Common Logarithms", 3),
        ("Irrational Numbers", 2),
    ]


def test_legacy_v1_pending_question_is_invalidated(monkeypatch):
    from app.services.lms import deficiency_chat_service as service

    question = {
        "source": "target_pdf",
        "generation_policy": "pdf_only_v1",
        "rag_thread_id": "thread-1",
        "question_text": "Legacy prompt-only question",
    }
    session = types.SimpleNamespace(
        id=5,
        student_id=7,
        status="active",
        current_index=0,
        questions_json="[]",
    )
    saved = {}

    class DB:
        def commit(self):
            saved["committed"] = True

    monkeypatch.setattr(service, "_load_questions", lambda current: [question])
    monkeypatch.setattr(
        service,
        "_save_questions",
        lambda current, questions: saved.update(questions=questions),
    )
    monkeypatch.setattr(service, "_reset_tutor_state", lambda current: None)
    monkeypatch.setattr(service, "get_db", lambda: DB())

    assert service._drop_non_pdf_pending(session, ["thread-1"]) is True
    assert saved["questions"] == []
    assert session.status == "completed"


def test_queue_runs_grounding_before_answer_key_and_drops_calculus(monkeypatch):
    from app.services.lms import deficiency_chat_service as service
    from app.services.quiz.models import MCQOption, MCQQuestion

    source = "Irrational numbers have non-terminating and non-recurring decimals."
    _fake_llm(
        monkeypatch,
        "Q0: SUPPORTED | Irrational numbers have non-terminating and non-recurring decimals.\n"
        "Q1: UNSUPPORTED",
    )
    monkeypatch.setattr(
        service,
        "_match_target_section",
        lambda name, threads: (name, "thread-1", 2),
    )
    monkeypatch.setattr(service, "get_section_text", lambda *args, **kwargs: source)

    def mcq(stem, correct):
        return MCQQuestion(
            question_text=stem,
            options=[MCQOption(label=label, text=value) for label, value in zip("ABCD", ("one", "two", "three", "four"))],
            correct_option_label=correct,
        )

    calls = {"count": 0}

    def generate(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] > 1:
            return []
        return [
            mcq("Which decimal form describes an irrational number?", "A"),
            mcq("What is the derivative of x squared?", "B"),
        ]

    monkeypatch.setattr(service, "generate_mcqs_from_content", generate)
    monkeypatch.setattr(
        "app.services.quiz.mcq_answer_check.verify_mcq_keys",
        lambda items: [{**item, "key_verified": "test"} for item in items],
    )
    queue = service._build_question_queue(
        [{
            "topic_id": 5,
            "topic_name": "Numbers",
            "slo_names": ["Irrational Numbers"],
            "score_percent": 80,
        }],
        ["thread-1"],
        9,
        "9",
    )
    assert [item["question_text"] for item in queue] == [
        "Which decimal form describes an irrational number?"
    ]
    assert queue[0]["generation_policy"] == "pdf_slo_only_v2"
