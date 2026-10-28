"""Answer-key gate for MCQs shown to students (Oct 2026 audit).

Learning Chat served AI questions with a wrong key or no correct option at all
(50 of 134 grade-fit AI questions), and Guided Practice served a QA leftover
"3+2=?" (options 1-4, keyed "4"). The LLM key check failed on Groq with
tool_use_failed 74 times and then let every question through.

Covers: the sympy verifier against 242 hand-checked questions, Diagnostic #3,
structural checks, the plain-text LLM verdicts, fail-closed behaviour, the
serve-time guard for queues built before the fix, manual question creation,
practice question picking, and the "in equality" word-split bug.
"""
import json
import pathlib
import re
import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.quiz import mcq_answer_check as mc
from app.services.quiz import mcq_math_verify as mv

FIXTURES = pathlib.Path(__file__).parent / "eval_fixtures"
AUDIT = json.loads((FIXTURES / "mcq_key_audit_2026_10_16.json").read_text(encoding="utf-8"))["items"]
DIAG3 = json.loads((FIXTURES / "diagnostic3_questions_2026_10_16.json").read_text(encoding="utf-8"))["items"]


def _opts(*texts):
    return [{"label": "ABCD"[i], "text": t} for i, t in enumerate(texts)]


def _q(stem, opts, key, **extra):
    return {"question_text": stem, "options": _opts(*opts), "correct_option_index": key, **extra}


# ── sympy verifier vs hand-checked keys ──────────────────────────────────────
def test_verifier_never_contradicts_a_hand_checked_key():
    """A correct question must never be dropped; a bad one must never be confirmed."""
    for item in AUDIT:
        v = mv.check_answer_key(item)
        if item["expected"] == "ok":
            assert not v.disproved, (item["n"], item["question_text"], v)
        else:
            assert v.status != mv.OK, (item["n"], item["question_text"], v)
        if item["expected"] == "wrong_key" and v.status == mv.WRONG_KEY:
            assert v.correct_indexes == (item["true_index"],), (item["n"], v)


def test_verifier_catches_most_bad_keys_without_an_llm():
    bad = [i for i in AUDIT if i["expected"] in ("wrong_key", "no_correct_option")]
    caught = [i for i in bad if mv.check_answer_key(i).disproved or mv.structural_problems(i)]
    assert len(bad) == 52
    assert len(caught) >= 39  # the rest (word problems, statements, bare equations) go to the LLM


@pytest.mark.parametrize(
    "stem, opts, key, status, right",
    [
        ("3+2=?", ("1", "2", "3", "4"), 3, mv.NO_CORRECT, ()),
        ("0+2=?", ("1", "2", "3", "4"), 0, mv.WRONG_KEY, (1,)),
        ("What is 2 + 2?", ("3", "4", "5", "6"), 1, mv.OK, (1,)),
        ("Simplify the expression: \\( -3 + 5 \\times (2 - 7) \\)", ("-28", "-22", "-18", "-12"), 1, mv.WRONG_KEY, (0,)),
        ("Solve for \\(x\\): \\(3(2x - 5) = 4x + 7\\).", ("x = 2", "x = 3", "x = 4", "x = 5"), 2, mv.NO_CORRECT, ()),
        ("Solve for \\(x\\): \\(3(2x - 5) = 4x + 1\\).", ("x = 6", "x = 7", "x = 8", "x = 9"), 2, mv.OK, (2,)),
        ("If \\(f(x) = 2x - 3\\), what is the value of \\(f(5)\\)?", ("7", "10", "13", "17"), 2, mv.WRONG_KEY, (0,)),
        ("Solve the system of equations: \\(2x + y = 11\\) and \\(3x - y = 4\\).",
         ("(x = 3, y = 5)", "(x = 5, y = 3)", "(x = 2, y = 7)", "(x = 4, y = 3)"), 0, mv.OK, (0,)),
        ("Solve the system of equations: \\[\\begin{cases} 2x + y = 7 \\\\ 3x - 2y = 4 \\end{cases}\\]",
         ("(x, y) = (2, 3)", "(x, y) = (1, 5)", "(x, y) = (3, 1)", "(x, y) = (4, -1)"), 2, mv.NO_CORRECT, ()),
        ("Write 0.0000923 in proper scientific notation.",
         ("9.23 \\times 10^{-5}", "9.23 \\times 10^{-4}", "9.23 \\times 10^{-6}", "9.23 \\times 10^{-7}"), 2, mv.WRONG_KEY, (0,)),
        ("Factor completely: \\(x^{2} - 5x + 6\\).", ("(x - 2)(x - 3)", "(x + 2)(x + 3)", "(x - 1)(x - 6)", "(x + 1)(x - 6)"), 0, mv.OK, (0,)),
        ("Solve for \\(x\\): \\(\\frac{2x-3}{4} = \\frac{x+5}{2} - 1\\).", ("x = 1", "x = 2", "x = 3", "x = 4"), 2, mv.NO_CORRECT, ()),
        ("Which equals four?", ("4", "√16", "5", "6"), 0, mv.MULTIPLE, (0, 1)),
    ],
)
def test_verifier_examples(stem, opts, key, status, right):
    v = mv.check_answer_key(_q(stem, opts, key))
    assert v.status == status
    assert v.correct_indexes == right


@pytest.mark.parametrize(
    "stem, opts",
    [
        ("Which of the following is a quadratic equation?", ("x^{2} - 5 = 0", "5 = 0", "x^{3} - 5 = 0", "2x - 5 = 0")),
        ("In a right-angled triangle, opposite = 4 and hypotenuse = 5. sin θ = ?", ("4 5", "3 5", "5 4", "1 2")),
        ("-2x<6", ("x<-3", "x>-3", "x<3", "x>3")),
        ("x^{2} + 6x + 9 = 0", ("-36", "0", "9", "36")),  # bare equation may ask the discriminant
        ("Calculate: \\frac{2+3i}{4-2i}", ("\\frac{7}{10} + \\frac{2}{5} i", "\\frac{1}{10} + \\frac{4}{5} i", "1", "2")),
        ("The perimeter of a rectangle is 54 cm. What is the width?", ("6 cm", "7 cm", "9 cm", "12 cm")),
    ],
)
def test_verifier_says_unknown_instead_of_guessing(stem, opts):
    assert not mv.check_answer_key(_q(stem, opts, 1)).disproved


def test_lowest_terms_question_is_not_multiple_correct():
    q = _q("Simplify the product and give the answer in lowest terms: \\( \\frac{3}{4} \\times \\frac{8}{9} \\)",
           ("\\(\\dfrac{2}{3}\\)", "\\(\\dfrac{1}{2}\\)", "\\(\\dfrac{24}{36}\\)", "\\(\\dfrac{3}{8}\\)"), 0)
    assert not mv.check_answer_key(q).disproved


# ── Diagnostic #3 is untouched ───────────────────────────────────────────────
def test_diagnostic3_questions_pass_every_check():
    assert len(DIAG3) == 20
    for q in DIAG3:
        assert mv.structural_problems(q) == [], q["id"]
        assert not mv.check_answer_key(q).disproved, q["id"]


def test_diagnostic3_keys_survive_the_gate_with_a_correct_llm(monkeypatch):
    """An LLM that solves correctly confirms every Diagnostic #3 key (nothing is lost)."""
    label_by_stem = {q["question_text"].strip(): "ABCD"[q["correct_option_index"]] for q in DIAG3}

    def solve(prompt):
        lines = []
        for n, stem in re.findall(r"^Q(\d+): (.+)$", prompt, flags=re.M):
            if stem.strip() in label_by_stem:
                lines.append(f"Q{n}: {label_by_stem[stem.strip()]}")
        return "\n".join(lines)

    _fake_llm(monkeypatch, responder=solve)
    assert [q["id"] for q in mc.verify_mcq_keys([dict(q) for q in DIAG3])] == [q["id"] for q in DIAG3]


# ── structural checks ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "item, problem",
    [
        (_q("What is 2 + 2?", ("3", "4", "5"), 1), "3 options (need 4)"),
        (_q("What is 2 + 2?", ("3", "4", "4", "6"), 1), "duplicate options"),
        (_q("What is 2+2?", ("4", "B", "5", "3"), 0), "option is only a letter"),
        (_q("What is 2 + 2?", ("3", "", "5", "6"), 1), "empty option"),
        (_q("What is 2 + 2?", ("3", "4", "5", "6"), 4), "answer key points to no option"),
        (_q("Solve the system: \\[\\begin{cases}^{2}y - x = 3\\\\ y + 2x = 7\\end{cases}\\]",
            ("(2, 5)", "(1, 4)", "(3, 2)", "(4, 1)"), 1), "malformed maths in stem or options"),
        (_q("Simplify \\( \\frac{4x}{y^2-1 \\)", ("1", "2", "3", "4"), 0), "malformed maths in stem or options"),
        (_q("Solve the quadratic equation by factorization.", ("x = 4 or x = -3", "x = 1", "x = 2", "x = 3"), 0),
         "instruction with no maths to work on"),
        (_q("", ("1", "2", "3", "4"), 0), "empty or too-short stem"),
    ],
)
def test_structural_problems(item, problem):
    assert problem in mv.structural_problems(item)


def test_concept_question_with_solve_word_is_not_structurally_broken():
    q = _q("Which method can be used to solve a quadratic equation?",
           ("Graphical method", "Long division", "Synthetic division", "Integration"), 0)
    assert mv.structural_problems(q) == []


# ── LLM checker: plain-text verdicts, fail-closed ────────────────────────────
def _fake_llm(monkeypatch, replies=None, error=None, responder=None):
    """LLM double: answers with ``responder(prompt)`` or ``replies`` in order, or raises
    ``error``; records prompts and bind() kwargs."""
    calls = {"prompts": [], "bind": []}
    queue = list(replies or [])

    class _LLM:
        model_name = "openai/gpt-oss-120b"

        def bind(self, **kwargs):
            calls["bind"].append(kwargs)
            return self

        def invoke(self, prompt):
            calls["prompts"].append(prompt)
            if error:
                raise error
            text = responder(prompt) if responder else (queue.pop(0) if queue else "")
            return types.SimpleNamespace(content=text)

    monkeypatch.setattr(mc, "get_chat_model", lambda **k: _LLM())
    monkeypatch.setattr(mc, "invoke_with_groq_rate_limit", lambda fn, **k: fn())
    return calls


WORDY = _q("The mean of 4, 8, 6, 5 and 3 is", ("5", "6", "5.2", "7"), 2)
WORDY2 = _q("The median of 1, 2, 3, 4 and 100 is", ("3", "2", "22", "4"), 0)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Q0: B\nQ1: NONE", {0: "B", 1: "NONE"}),
        ("**Q0:** C\n- Q1) a", {0: "C", 1: "A"}),
        ("Q0: NONE  \nQ1: A  \nQ2: A", {0: "NONE", 1: "A", 2: "A"}),  # the reply Groq rejected as tool_use_failed
        ("Answer: B", {}),
        ("Q0: B\nQ0: C", {0: "B"}),
    ],
)
def test_parse_verdicts(text, expected):
    assert mc.parse_verdicts(text) == expected


def test_llm_confirms_key_from_plain_text(monkeypatch):
    calls = _fake_llm(monkeypatch, replies=["Q0: C\nQ1: NONE"])
    kept = mc.verify_mcq_keys([WORDY, WORDY2])
    assert [q["question_text"] for q in kept] == [WORDY["question_text"]]
    assert kept[0]["key_verified"] == "llm"
    assert "Q0: B" in calls["prompts"][0]  # asks for the plain-text format
    assert calls["bind"][0] == {"reasoning_effort": "medium"}


@pytest.mark.parametrize("reply", ["Q0: A", "Q0: NONE", "", "I think the answer is C."])
def test_llm_disagreement_or_no_verdict_drops_the_question(monkeypatch, reply):
    _fake_llm(monkeypatch, replies=[reply, reply])
    assert mc.verify_mcq_keys([WORDY]) == []


def test_checker_failure_is_fail_closed(monkeypatch):
    _fake_llm(monkeypatch, error=RuntimeError("Error code: 400 - tool_use_failed"))
    assert mc.verify_mcq_keys([WORDY, WORDY2]) == []


def test_checker_failure_still_keeps_math_confirmed_questions(monkeypatch):
    _fake_llm(monkeypatch, error=RuntimeError("llm down"))
    ok = _q("What is 2 + 2?", ("3", "4", "5", "6"), 1)
    kept = mc.verify_mcq_keys([WORDY, ok])
    assert [q["question_text"] for q in kept] == ["What is 2 + 2?"]
    assert kept[0]["key_verified"] == "math"


def test_disproved_key_is_dropped_even_if_the_llm_agrees(monkeypatch):
    calls = _fake_llm(monkeypatch, replies=["Q0: D"])
    bad = _q("3+2=?", ("1", "2", "3", "4"), 3)
    assert mc.verify_mcq_keys([bad]) == []
    assert calls["prompts"] == []  # sympy settled it, no LLM call


def test_structurally_broken_question_never_reaches_the_llm(monkeypatch):
    calls = _fake_llm(monkeypatch, replies=["Q0: A"])
    assert mc.verify_mcq_keys([_q("What is 2+2?", ("4", "B", "5", "3"), 0)]) == []
    assert calls["prompts"] == []


def test_llm_is_asked_in_small_batches(monkeypatch):
    items = [_q(f"Question {i}: the mean of {i}, {i + 2} and {i + 4} is", (str(i + 2), "0", "1", "100"), 0) for i in range(7)]
    calls = _fake_llm(monkeypatch, replies=["\n".join(f"Q{i}: A" for i in range(5)), "Q0: A\nQ1: A"])
    assert len(mc.verify_mcq_keys(items)) == 7
    assert len(calls["prompts"]) == 2


def test_verify_mcq_models_keeps_only_confirmed(monkeypatch):
    from app.services.quiz.models import MCQOption, MCQQuestion

    _fake_llm(monkeypatch, error=RuntimeError("down"))

    def mcq(stem, texts, label):
        return MCQQuestion(question_text=stem, options=[MCQOption(label="ABCD"[i], text=t) for i, t in enumerate(texts)],
                           correct_option_label=label)

    good = mcq("What is 5 * 6?", ["30", "11", "56", "25"], "A")
    bad = mcq("3+2=?", ["1", "2", "3", "4"], "D")
    wordy = mcq("The mean of 4, 8 and 6 is", ["6", "5", "7", "8"], "A")
    assert mc.verify_mcq_models([good, bad, wordy]) == [good]


# ── Learning Chat: queue top-up and serve-time guard ─────────────────────────
def test_queue_tops_up_twice_from_pdf_when_the_check_drops_questions(monkeypatch):
    from app.services.lms import deficiency_chat_service as svc

    counter = {"n": 0, "pdf": 0}

    def item(diff):
        counter["n"] += 1
        return {"topic_id": 1, "question_text": f"question {counter['n']}", "difficulty": diff,
                "options": _opts("a", "b", "c", "d"), "correct_option_index": 0}

    monkeypatch.setattr(svc, "_match_target_section", lambda name, threads: ("S", "t1", 1.0))
    monkeypatch.setattr(svc, "get_section_text", lambda *args, **kwargs: "Relevant PDF text")

    def from_pdf(text, section, count, **kwargs):
        assert kwargs["strict_grounding"] is True
        counter["pdf"] += 1
        return list(range(count))

    monkeypatch.setattr(svc, "generate_mcqs_from_content", from_pdf)
    monkeypatch.setattr(
        svc, "_mcq_to_queue_item",
        lambda mcq, tid, name, section, thread, diff: {
            **item(diff), "source": "target_pdf", "rag_thread_id": thread,
        },
    )
    # first call keeps nothing, later calls keep everything
    seen = {"calls": 0}

    def verify(items):
        seen["calls"] += 1
        return [] if seen["calls"] <= 2 else items

    monkeypatch.setattr("app.services.quiz.mcq_answer_check.verify_mcq_keys", verify)
    queue = svc._build_question_queue([{"topic_id": 1, "topic_name": "Algebra", "score_percent": 50}], ["t1"], 9, "9")
    assert len(queue) == 5
    assert counter["pdf"] == 3
    assert {q["source"] for q in queue} == {"target_pdf"}


@pytest.fixture()
def chat_db(monkeypatch):
    from app.models.lms_models import Base, DeficiencyChatSession
    from app.services.lms import deficiency_chat_service as svc

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DeficiencyChatSession.__table__])
    session = sessionmaker(bind=engine)()
    done = {"scores": [], "path": []}
    monkeypatch.setattr(svc, "get_db", lambda: session)
    monkeypatch.setattr(svc, "_mark_learning_path_chat_complete", lambda sid: done["path"].append(sid))
    import app.services.lms.performance_service as ps

    monkeypatch.setattr(ps, "update_topic_scores_from_deficiency_session", lambda sid: done["scores"].append(sid))
    yield session, done
    session.close()


def _chat(session, questions, current_index=0, status="active"):
    from app.models.lms_models import DeficiencyChatSession

    s = DeficiencyChatSession(student_id=7, questions_json=json.dumps(questions), current_index=current_index,
                              correct_count=0, status=status, weak_topics_json="[]", chat_history_json="{}")
    session.add(s)
    session.commit()
    return s


GOOD = {**_q("What is 5 * 6?", ("30", "11", "56", "25"), 0), "topic_id": 1, "answered": False, "correct": None}
BAD = {**_q("Solve for \\(x\\): \\(3(2x - 5) = 4x + 7\\).", ("x = 2", "x = 3", "x = 4", "x = 5"), 2),
       "topic_id": 1, "answered": False, "correct": None}
UNREADABLE = {**WORDY, "topic_id": 1, "answered": False, "correct": None}


def test_serve_time_guard_removes_pending_disproved_questions(chat_db):
    from app.services.lms import deficiency_chat_service as svc

    session, _ = chat_db
    s = _chat(session, [GOOD, BAD, UNREADABLE, BAD])
    state = svc._session_state(s)
    texts = [q["question_text"] for q in json.loads(s.questions_json)]
    assert texts == [GOOD["question_text"], UNREADABLE["question_text"]]
    assert state["total_questions"] == 2
    assert state["current_question"]["question_text"] == GOOD["question_text"]
    assert "correct_option_index" not in state["current_question"]


def test_serve_time_guard_skips_the_current_bad_question_and_keeps_history(chat_db):
    from app.services.lms import deficiency_chat_service as svc

    session, _ = chat_db
    answered_bad = {**BAD, "answered": True, "correct": False}
    history = {**BAD, "answered": True, "correct": True}  # already in the past: left alone
    s = _chat(session, [history, answered_bad, GOOD], current_index=1)
    state = svc._session_state(s)
    qs = json.loads(s.questions_json)
    assert len(qs) == 2 and qs[0]["correct"] is True
    assert state["current_question"]["question_text"] == GOOD["question_text"]


def test_serve_time_guard_completes_a_session_with_nothing_valid_left(chat_db):
    from app.services.lms import deficiency_chat_service as svc

    session, done = chat_db
    s = _chat(session, [{**GOOD, "answered": True, "correct": True}, BAD], current_index=1)
    state = svc._session_state(s)
    assert state["completed"] is True
    assert done == {"scores": [s.id], "path": [7]}


def test_serve_time_guard_leaves_verified_and_finished_sessions_alone(chat_db):
    from app.services.lms import deficiency_chat_service as svc

    session, _ = chat_db
    s = _chat(session, [{**BAD, "key_verified": "llm"}])
    svc._session_state(s)
    assert len(json.loads(s.questions_json)) == 1
    done_session = _chat(session, [BAD], status="completed")
    svc._session_state(done_session)
    assert len(json.loads(done_session.questions_json)) == 1


# ── manual question creation and practice picking ────────────────────────────
@pytest.fixture()
def bank_db(monkeypatch):
    from app.models.lms_models import Base, Question, Topic
    from app.services.lms import practice_service, question_bank_service

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Topic.__table__, Question.__table__])
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(question_bank_service, "get_db", lambda: session)
    monkeypatch.setattr(practice_service, "get_db", lambda: session)
    yield session
    session.close()


def test_manual_question_with_a_wrong_key_is_refused(bank_db):
    from app.services.lms import question_bank_service as qb
    from app.services.lms.exceptions import LMSValidationError

    with pytest.raises(LMSValidationError, match="None of the options"):
        qb.create_question(1, "3+2=?", ["1", "2", "3", "4"], 3, topic_id=4)
    with pytest.raises(LMSValidationError, match="option B"):
        qb.create_question(1, "0+2=?", ["1", "2", "3", "4"], 0, topic_id=4)
    assert qb.create_question(1, "What is 2 + 2?", ["3", "4", "5", "6"], 1).id
    # a teacher's own PDF answer key is logged, not refused
    assert qb.create_question(1, "0+2=?", ["1", "2", "3", "4"], 0, source_type="pdf_qa_converted").id


def test_manual_question_update_is_checked_too(bank_db):
    from app.services.lms import question_bank_service as qb
    from app.services.lms.exceptions import LMSValidationError

    q = qb.create_question(1, "What is 2 + 2?", ["3", "4", "5", "6"], 1)
    with pytest.raises(LMSValidationError):
        qb.update_question(q.id, options=["3", "4", "5", "6"], correct_option_index=0)


def test_practice_never_picks_a_disproved_question(bank_db):
    from app.models.lms_models import Question
    from app.services.lms import practice_service

    good = Question(id=1, topic_id=4, question_text="What is 2 + 2?", options_json=json.dumps(_opts("3", "4", "5", "6")),
                    correct_option_index=1, is_active=True)
    junk = Question(id=2, topic_id=4, question_text="3+2=?", options_json=json.dumps(_opts("1", "2", "3", "4")),
                    correct_option_index=3, is_active=True)
    bank_db.add_all([good, junk])
    bank_db.commit()
    q = practice_service._first_usable(bank_db.query(Question).filter(Question.topic_id == 4).order_by(Question.id.desc()))
    assert q.id == 1


# ── split words ("in equality") ──────────────────────────────────────────────
@pytest.mark.parametrize("word", ["inequality", "Rationalize", "fractional", "infinitely", "Integration"])
def test_real_words_are_not_split(word):
    from app.services.quiz.display_format_qa import finalize_display_text
    from app.services.quiz.math_text import unsquash_english

    assert unsquash_english(f"the {word} here") == f"the {word} here"
    assert word in finalize_display_text(f"What about the {word}?", None)[0]


def test_smashed_exam_text_is_still_unsquashed():
    from app.services.quiz.math_text import unsquash_english

    assert unsquash_english("Whichisthecorrectfactorization") == "Which is the correct factorization"


def test_stored_split_words_are_rejoined_for_display():
    from app.services.quiz.display_format_qa import finalize_display_text, finalize_option_text

    assert "inequality" in finalize_display_text("What is the solution set for the in equality \\(5 - 2x > 9\\)?")[0]
    assert finalize_option_text("Rational ize the denominator")[0].startswith("Rationalize")
