"""AI Guided Practice: Target-PDF topic hierarchy, version-scoped question pools,
fresh-question selection, shuffling, PDF replacement and session APIs.

Runs the real service on in-memory SQLite. Only the LLM-backed generation and the
RAG chunk store are stubbed: the stub "PDF" is a set of pages whose text holds
numbered facts, and the stub generator writes one question per fact it is given,
so every assertion about grounding / topic scope / exhaustion is checked against
what the service actually fed the generator.
"""
import json
import random
import re
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.lms_models import (
    AIGPPdfVersion,
    AIGPQuestion,
    AIGPSession,
    AIGPSessionItem,
    AIGPTopic,
    Base,
)
from app.services.lms import ai_guided_practice_service as svc
from app.services.lms import ai_guided_practice_topics as topics_mod
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError

STUDENT_A = 701
STUDENT_B = 702

# PDF v1: two units; Unit 1 has two numbered sections. Facts per section are finite.
PDF_V1 = {
    1: "UNIT 1 Real Numbers\nIntro to real numbers.",
    2: "1.1 Rational Numbers\n" + " ".join(f"RATFACT{i} rational fact {i}." for i in range(14)),
    3: "1.2 Irrational Numbers\n" + " ".join(f"IRRFACT{i} irrational fact {i}." for i in range(6)),
    4: "UNIT 2 Logarithms\n" + " ".join(f"LOGFACT{i} logarithm fact {i}." for i in range(25)),
}
# PDF v2 (replacement): different structure.
PDF_V2 = {
    1: "UNIT 1 Sets\nIntro to sets.",
    2: "1.1 Venn Diagrams\n" + " ".join(f"VENNFACT{i} venn fact {i}." for i in range(30)),
}


def _chunks(pages):
    return [{"page": p, "text": t, "chunk_index": i} for i, (p, t) in enumerate(sorted(pages.items()))]


class FakeGenerator:
    """One MCQ per FACT token present in the given text, skipping excluded ones."""

    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, text, scope, ladder, grade_level, exclude, thread_id):
        self.calls.append({"text": text, "scope": scope, "n": len(ladder), "thread": thread_id})
        if self.fail:
            raise RuntimeError("LLM down")
        excluded = " ".join(exclude)
        facts = [f for f in dict.fromkeys(re.findall(r"[A-Z]+FACT\d+", text)) if f not in excluded]
        out = []
        for i, fact in enumerate(facts[: len(ladder)]):
            out.append(
                {
                    "question_text": f"Which statement about {fact} is in the text?",
                    "options": [
                        {"label": "A", "text": f"{fact} correct"},
                        {"label": "B", "text": f"{fact} wrong one"},
                        {"label": "C", "text": f"{fact} wrong two"},
                        {"label": "D", "text": f"{fact} wrong three"},
                    ],
                    "correct_option_index": 0,
                    "difficulty": ladder[i],
                    "explanation": f"The PDF states {fact}.",
                    "key_verified": "llm",
                }
            )
        return out


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()

    import app.utils.db as db_mod
    from app.tasks import lms_tasks

    monkeypatch.setattr(db_mod, "get_session_factory", lambda: (lambda: s))
    state = SimpleNamespace(
        pdfs={"thread-v1": PDF_V1},
        targets=[{"id": 1, "rag_thread_id": "thread-v1", "original_filename": "target_v1.pdf"}],
        diag=SimpleNamespace(id=50, grade_level="9", created_by=1),
        gen=FakeGenerator(),
        topups=[],
    )
    monkeypatch.setattr(svc, "_student_target_pdfs", lambda sid: (state.diag, list(state.targets)))
    monkeypatch.setattr(svc, "_thread_chunks", lambda v: _chunks(state.pdfs.get(v.rag_thread_id, {})))
    monkeypatch.setattr(svc, "_thread_headings", lambda v: [])
    monkeypatch.setattr(svc, "_generate_candidates", state.gen)
    monkeypatch.setattr(svc, "_render", lambda text, latex, inline: text or "")
    monkeypatch.setattr(svc, "_has_positional_option", lambda opts: False)
    monkeypatch.setattr(svc, "LOCK_WAIT_SECONDS", 0.5)
    svc._last_enqueued.clear()
    # Background processing runs inline (no LLM: numbering heuristic decides the levels).
    monkeypatch.setattr(
        lms_tasks, "enqueue_ai_guided_practice_processing", lambda vid: svc.process_version(vid, use_llm=False)
    )
    monkeypatch.setattr(lms_tasks, "enqueue_ai_guided_practice_topup", lambda sid, n: state.topups.append((sid, n)))
    app = Flask(__name__)
    with app.app_context():
        state.db = s
        yield state
    s.close()


def _sub(dash, title):
    for src in dash["sources"]:
        for t in src["topics"]:
            for sub in t["subtopics"]:
                if sub["title"] == title:
                    return sub
    raise AssertionError(f"subtopic {title!r} not in dashboard: {json.dumps(dash)[:500]}")


def _qids(state_):
    return [it["question_text"] for it in state_["items"]]


def _answer_all(sid, session_state, correct=True):
    for item in session_state["items"]:
        # Find the displayed position of the canonical correct option ("... correct").
        pos = next(o["position"] for o in item["options"] if o["text"].endswith(" correct"))
        if not correct:
            pos = next(o["position"] for o in item["options"] if not o["text"].endswith(" correct"))
        svc.submit_answer(sid, session_state["session_id"], item["position"], pos)


# -- hierarchy ------------------------------------------------------------------------


def test_dashboard_shows_pdf_hierarchy_without_diagnostic(env):
    dash = svc.dashboard(STUDENT_A)
    assert dash["state"] == "ready"
    topics = dash["sources"][0]["topics"]
    assert [t["title"] for t in topics] == ["UNIT 1 Real Numbers", "UNIT 2 Logarithms"]
    assert [s["title"] for s in topics[0]["subtopics"]] == ["1.1 Rational Numbers", "1.2 Irrational Numbers"]
    # Unit 2 printed no subtopics: one explicit whole-section entry, nothing invented.
    unit2 = topics[1]["subtopics"]
    assert len(unit2) == 1 and unit2[0]["is_whole_section"] and unit2[0]["title"] == "UNIT 2 Logarithms"
    assert dash["session_size"] == 10


def test_no_active_pdf_and_failed_processing_states(env):
    env.targets = []
    assert svc.dashboard(STUDENT_A)["state"] == "no_active_pdf"
    env.targets = [{"id": 9, "rag_thread_id": "thread-junk", "original_filename": "junk.pdf"}]
    env.pdfs["thread-junk"] = {1: "just some prose without any headings at all"}
    dash = svc.dashboard(STUDENT_A)
    assert dash["state"] == "failed"
    assert dash["failed"][0]["filename"] == "junk.pdf"
    assert "error" not in dash["failed"][0]  # admin-side error text is not sent to students (B13)
    bad = env.db.query(AIGPPdfVersion).filter_by(rag_thread_id="thread-junk").one()
    assert bad.error_message  # ...but it is recorded for the admin panel


def test_llm_titles_not_in_pdf_are_dropped(monkeypatch):
    pages = topics_mod.page_texts(_chunks(PDF_V1))
    cands = topics_mod.collect_candidates(pages, [], [])
    fake = topics_mod._LLMHierarchy(
        main_topics=[
            topics_mod._LLMMain(
                candidate_index=0,
                subtopics=[
                    topics_mod._LLMSub(candidate_index=1),
                    topics_mod._LLMSub(title="Quantum Field Theory", page=2),  # invented
                    topics_mod._LLMSub(title="Intro to real numbers", page=1),  # verbatim in text
                ],
            ),
            topics_mod._LLMMain(title="Calculus of Variations", page=4),  # invented main
        ]
    )
    import app.utils.groq_rate_limit as grl

    monkeypatch.setattr(grl, "invoke_with_groq_rate_limit", lambda fn, **k: fake)
    import app.utils.llm_factory as lf

    monkeypatch.setattr(lf, "get_chat_model", lambda **k: None)
    mains = topics_mod._llm_organise(cands, pages, "x.pdf")
    assert [m["title"] for m in mains] == [cands[0]["title"]]
    sub_titles = [s["title"] for s in mains[0]["subtopics"]]
    assert "Quantum Field Theory" not in sub_titles
    assert "Intro to real numbers" in sub_titles


def test_pdf_outline_is_used_when_two_levels():
    outline = [
        {"level": 1, "title": "Unit 1 Sets", "page": 1},
        {"level": 2, "title": "Union", "page": 2},
        {"level": 2, "title": "Intersection", "page": 3},
        {"level": 1, "title": "Unit 2 Logic", "page": 5},
    ]
    r = topics_mod.extract_hierarchy(_chunks({1: "a", 2: "b", 3: "c", 5: "d", 6: "e"}), [], outline, use_llm=False)
    assert r["structure"] == "outline"
    assert [s["title"] for s in r["main_topics"][0]["subtopics"]] == ["Union", "Intersection"]
    # Inclusive of the page where the next unit starts (it may start mid-page).
    assert r["main_topics"][0]["page_end"] == 5 and r["main_topics"][1]["page_end"] == 6


# Same shape as the real Grade 9 Target PDF: "UNIT n" headings + a printed "Topics:" line.
PDF_TOPIC_LISTS = {
    1: "UNIT 8: Quadratic Equations\nTopics: Quadratic Equations, Completing the Square, Quadratic\nFormula\n"
       "Exam weightage: 8%\n"
       "QFACT1 completing square. QFACT2 quadratic formula.\n"
       "UNIT 9: Congruent Triangles\nTopics: SSS, SAS\nTRIFACT1 sss rule. TRIFACT2 sas rule.",
    2: "TRIFACT3 more triangles.\nUNIT 10: Parallelograms\nPARAFACT1 opposite sides.",
}


def test_printed_topic_lists_become_subtopics(env):
    r = topics_mod.extract_hierarchy(_chunks(PDF_TOPIC_LISTS), [], [], use_llm=False)
    assert r["structure"] == "topic_lists"
    by_main = {m["title"]: m for m in r["main_topics"]}
    # "Quadratic Equations" repeats the unit name, so it is not a separate subtopic.
    assert [s["title"] for s in by_main["UNIT 8: Quadratic Equations"]["subtopics"]] == [
        "Completing the Square", "Quadratic Formula"]
    assert [s["title"] for s in by_main["UNIT 9: Congruent Triangles"]["subtopics"]] == ["SSS", "SAS"]
    assert by_main["UNIT 10: Parallelograms"]["subtopics"][0]["is_whole_section"]


def test_topic_list_subtopic_reads_its_unit_section_only(env):
    env.pdfs["thread-v1"] = PDF_TOPIC_LISTS
    sub = _sub(svc.dashboard(STUDENT_A), "SAS")
    svc.start_session(STUDENT_A, sub["subtopic_id"])
    text = env.gen.calls[0]["text"]
    assert "TRIFACT1" in text and "TRIFACT3" in text  # unit continues onto page 2
    assert "QFACT" not in text and "PARAFACT" not in text  # cut at neighbouring unit headings
    # Scope = the subtopic alone, so the grounding judge rejects other parts of the unit.
    assert env.gen.calls[0]["scope"] == "SAS (only this subtopic; out of scope: SSS)"


def test_focus_excerpt_keeps_subtopic_lines():
    unit = "\n".join([
        "Topics: SSS, SAS, ASA",
        "Two triangles are congruent if all parts match.",
        "SSS: three sides of one equal to three corresponding sides of the other, so the triangles match exactly.",
        "Example: sides 3, 4, 5 and 3, 4, 5 are congruent by SSS which needs three equal sides each time.",
        "ASA: two angles and the included side.",
        "Line five.",
        "Line six.",
        "AAS: two angles and a non-included side.",
    ])
    out = svc.focus_excerpt(unit, "SSS", context_lines=0)
    assert "three sides" in out and "Example" in out and "AAS" not in out and "Topics:" not in out
    assert svc.focus_excerpt(unit, "Pythagoras") == unit  # nothing matches -> full text


# -- generation, grounding, insufficient content -----------------------------------


def test_first_session_has_ten_grounded_questions_from_subtopic_pages_only(env):
    sub = _sub(svc.dashboard(STUDENT_A), "1.1 Rational Numbers")
    st = svc.start_session(STUDENT_A, sub["subtopic_id"])
    assert st["question_count"] == 10 and len(st["items"]) == 10
    assert all("RATFACT" in q for q in _qids(st))
    call = env.gen.calls[0]
    assert "RATFACT" in call["text"] and "LOGFACT" not in call["text"] and "IRRFACT" not in call["text"]
    assert call["scope"] == "1.1 Rational Numbers (only this subtopic; out of scope: 1.2 Irrational Numbers)"
    q = env.db.query(AIGPQuestion).first()
    assert q.source_page == 2 and q.explanation.startswith("The PDF states")
    # Key never sent before answering.
    assert "correct_position" not in st["items"][0]


def test_insufficient_content_returns_state_not_fabricated_questions(env):
    env.pdfs["thread-v1"] = {**PDF_V1, 3: "1.2 Irrational Numbers\n(no text on this page)"}
    sub = _sub(svc.dashboard(STUDENT_A), "1.2 Irrational Numbers")
    r = svc.start_session(STUDENT_A, sub["subtopic_id"])
    assert r["session"] is None and r["reason"] in ("insufficient_content", "generation_failed")
    assert env.db.query(AIGPQuestion).count() == 0


def test_small_pool_gives_short_session_without_duplicates(env):
    sub = _sub(svc.dashboard(STUDENT_A), "1.2 Irrational Numbers")  # only 6 facts
    st = svc.start_session(STUDENT_A, sub["subtopic_id"])
    assert st["question_count"] == 6
    assert len(set(_qids(st))) == 6


def test_generation_failure_is_reported(env):
    env.gen.fail = True
    sub = _sub(svc.dashboard(STUDENT_A), "1.1 Rational Numbers")
    r = svc.start_session(STUDENT_A, sub["subtopic_id"])
    assert r["session"] is None and r["reason"] == "generation_failed"


def test_real_pipeline_drops_ungrounded_and_wrong_key_items(monkeypatch):
    """_generate_candidates chains generator -> grounding check -> key check."""
    import app.services.quiz.diagnostic_generator as dg
    import app.services.quiz.mcq_answer_check as ac
    import app.services.quiz.mcq_grounding_check as gc
    from app.services.quiz.models import MCQOption, MCQQuestion

    def mk(text):
        return MCQQuestion(
            question_text=text,
            options=[MCQOption(label=l, text=f"{text} {l}") for l in "ABCD"],
            correct_option_label="A",
            explanation="because",
        )

    monkeypatch.setattr(dg, "generate_mcqs_from_content", lambda *a, **k: [mk("What is grounded one?"), mk("What is invented two?")])
    monkeypatch.setattr(gc, "verify_mcq_grounding", lambda items, src, scope: [i for i in items if "grounded" in i["question_text"]])
    monkeypatch.setattr(ac, "verify_mcq_keys", lambda items: [{**i, "key_verified": "math"} for i in items])
    out = svc._generate_candidates("text", "Scope", ["easy", "medium"], "9", [], "t")
    assert [i["question_text"] for i in out] == ["What is grounded one?"]
    assert out[0]["explanation"] == "because" and out[0]["key_verified"] == "math"


# -- fresh questions, multiple students, exhaustion, shuffling ----------------------


def test_second_attempt_gets_different_questions(env):
    sub = _sub(svc.dashboard(STUDENT_A), "UNIT 2 Logarithms")  # 25 facts
    first = svc.start_session(STUDENT_A, sub["subtopic_id"])
    _answer_all(STUDENT_A, first)
    second = svc.start_session(STUDENT_A, sub["subtopic_id"])
    assert second["session_id"] != first["session_id"]
    assert not set(_qids(first)) & set(_qids(second))
    assert second["reused_count"] == 0


def test_other_student_reuses_shared_pool_without_new_llm_calls(env):
    sub = _sub(svc.dashboard(STUDENT_A), "UNIT 2 Logarithms")
    a = svc.start_session(STUDENT_A, sub["subtopic_id"])
    calls_after_a = len(env.gen.calls)
    b = svc.start_session(STUDENT_B, sub["subtopic_id"])
    assert len(env.gen.calls) == calls_after_a  # pool already had 10 unseen for B
    assert len(b["items"]) == 10
    assert set(_qids(a)) == set(_qids(b))  # same validated pool, B's own history
    assert b["reused_count"] == 0


def test_exhausted_pool_reuses_least_recent_with_shuffle(env):
    sub_id = _sub(svc.dashboard(STUDENT_A), "1.1 Rational Numbers")["subtopic_id"]  # 14 facts
    s1 = svc.start_session(STUDENT_A, sub_id)
    _answer_all(STUDENT_A, s1)
    s2 = svc.start_session(STUDENT_A, sub_id)
    _answer_all(STUDENT_A, s2)
    assert env.db.query(AIGPQuestion).filter_by(topic_id=sub_id).count() == 14
    # s2 = 4 unseen + 6 reused from s1 (least recently seen); no duplicate inside a session.
    assert s2["question_count"] == 10 and s2["reused_count"] == 6
    assert len(set(_qids(s2))) == 10
    assert len(set(_qids(s2)) - set(_qids(s1))) == 4
    s3 = svc.start_session(STUDENT_A, sub_id)  # second dry round -> pool exhausted
    _answer_all(STUDENT_A, s3)
    env.db.expire_all()
    assert env.db.query(AIGPTopic).get(sub_id).pool_exhausted
    assert s3["question_count"] == 10 and len(set(_qids(s3))) == 10
    assert _qids(s3) != _qids(s2)
    calls = len(env.gen.calls)
    s4 = svc.start_session(STUDENT_A, sub_id)  # exhausted: reuse only, no LLM call
    assert len(env.gen.calls) == calls
    assert s4["reused_count"] == 10 and len(set(_qids(s4))) == 10
    # Least recently seen first: the 4 questions not in s3 must be in s4.
    assert set(_qids(s1) + _qids(s2)) - set(_qids(s3)) <= set(_qids(s4))


def test_select_questions_unit():
    pool = [(i, ["easy", "medium", "hard"][i % 3]) for i in range(1, 7)]
    t0 = datetime(2026, 1, 1)
    history = {1: (t0, 1), 2: (t0 + timedelta(days=1), 1), 3: (t0 + timedelta(days=2), 1)}
    picked, reused = svc.select_questions(pool, history, 5, rng=random.Random(3))
    assert set(picked) >= {4, 5, 6} and reused == 2
    assert set(picked) - {4, 5, 6} == {1, 2}  # least recently seen
    picked_all, _ = svc.select_questions(pool, {}, 10, rng=random.Random(1))
    assert sorted(picked_all) == [1, 2, 3, 4, 5, 6]  # never padded with duplicates


def test_option_shuffle_keeps_correct_answer_mapping(env):
    random.seed(7)
    sub_id = _sub(svc.dashboard(STUDENT_A), "UNIT 2 Logarithms")["subtopic_id"]
    st = svc.start_session(STUDENT_A, sub_id)
    orders = {it.option_order_json for it in env.db.query(AIGPSessionItem).all()}
    assert len(orders) > 1  # options were actually shuffled
    for item in st["items"]:
        pos = next(o["position"] for o in item["options"] if o["text"].endswith(" correct"))
        r = svc.submit_answer(STUDENT_A, st["session_id"], item["position"], pos)
        assert r["correct"] and r["correct_position"] == pos
        assert r["explanation"] and r["source_page"] == 4
    done = svc.session_state(STUDENT_A, st["session_id"])
    assert done["status"] == "completed" and done["score_percent"] == 100.0


def test_open_session_is_resumed_and_request_key_is_idempotent(env):
    sub_id = _sub(svc.dashboard(STUDENT_A), "UNIT 2 Logarithms")["subtopic_id"]
    a = svc.start_session(STUDENT_A, sub_id, request_key="k1")
    again = svc.start_session(STUDENT_A, sub_id, request_key="k1")
    resumed = svc.start_session(STUDENT_A, sub_id)
    assert a["session_id"] == again["session_id"] == resumed["session_id"]
    assert resumed["resumed"]
    assert env.db.query(AIGPSession).count() == 1


# -- authorization -----------------------------------------------------------------


def test_students_cannot_touch_other_students_sessions(env):
    sub_id = _sub(svc.dashboard(STUDENT_A), "UNIT 2 Logarithms")["subtopic_id"]
    st = svc.start_session(STUDENT_A, sub_id)
    with pytest.raises(LMSNotFoundError):
        svc.session_state(STUDENT_B, st["session_id"])
    with pytest.raises(LMSNotFoundError):
        svc.submit_answer(STUDENT_B, st["session_id"], 0, 0)


def test_routes_are_student_only_and_work_for_students(env, monkeypatch):
    import app.utils.auth as auth
    from app.routes import lms_routes

    monkeypatch.setattr(auth, "sync_session_user", lambda: True)
    app = Flask("routes")
    app.secret_key = "t"
    app.register_blueprint(lms_routes.bp, url_prefix="/api/lms")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"], sess["role"] = STUDENT_A, "teacher"
    assert client.get("/api/lms/ai-guided-practice/dashboard").status_code == 403
    assert client.post("/api/lms/ai-guided-practice/sessions", json={"subtopic_id": 1}).status_code == 403
    with client.session_transaction() as sess:
        sess["role"] = "student"
    dash = client.get("/api/lms/ai-guided-practice/dashboard").get_json()
    data = dash.get("data", dash)
    sub_id = _sub(data, "UNIT 2 Logarithms")["subtopic_id"]
    started = client.post("/api/lms/ai-guided-practice/sessions", json={"subtopic_id": sub_id}).get_json()
    st = started.get("data", started)
    assert len(st["items"]) == 10
    r = client.post(
        f"/api/lms/ai-guided-practice/sessions/{st['session_id']}/answer",
        json={"position": 0, "option_position": 0},
    )
    assert r.status_code == 200
    assert client.get("/api/lms/ai-guided-practice/sessions/999999").status_code == 404


# -- PDF replacement / versioning -------------------------------------------------


def test_replacement_switches_topics_and_keeps_history(env):
    sub_id = _sub(svc.dashboard(STUDENT_A), "UNIT 2 Logarithms")["subtopic_id"]
    old = svc.start_session(STUDENT_A, sub_id)
    svc.submit_answer(STUDENT_A, old["session_id"], 0, 0)  # in progress under v1

    # Admin replaces the Target PDF.
    env.pdfs["thread-v2"] = PDF_V2
    env.targets = [{"id": 2, "rag_thread_id": "thread-v2", "original_filename": "target_v2.pdf"}]
    dash = svc.dashboard(STUDENT_A)
    titles = [t["title"] for src in dash["sources"] for t in src["topics"]]
    assert titles == ["UNIT 1 Sets"]
    assert dash["sources"][0]["filename"] == "target_v2.pdf"

    # New sessions only from v2.
    with pytest.raises(LMSValidationError):
        svc.start_session(STUDENT_A, sub_id)
    new = svc.start_session(STUDENT_A, _sub(dash, "1.1 Venn Diagrams")["subtopic_id"])
    v2 = env.db.query(AIGPPdfVersion).filter_by(rag_thread_id="thread-v2").one()
    q_versions = {
        q.version_id
        for q in env.db.query(AIGPQuestion)
        .join(AIGPSessionItem, AIGPSessionItem.question_id == AIGPQuestion.id)
        .filter(AIGPSessionItem.session_id == new["session_id"])
    }
    assert q_versions == {v2.id}
    assert all("VENNFACT" in q for q in _qids(new))

    # The in-progress v1 session stays consistent and can be finished.
    old_state = svc.session_state(STUDENT_A, old["session_id"])
    assert all("LOGFACT" in q for q in _qids(old_state)) and old_state["answered_count"] == 1
    done = svc.complete_session(STUDENT_A, old["session_id"])
    assert done["status"] == "completed"
    hist = {h["session_id"]: h for h in svc.history(STUDENT_A)}
    assert hist[old["session_id"]]["is_current_pdf"] is False
    # New version does not inherit v1 progress.
    assert _sub(svc.dashboard(STUDENT_A), "1.1 Venn Diagrams")["status"] == "in_progress"


def test_removed_pdf_is_not_revived_when_its_successor_fails(env):
    """B5: an admin who REMOVES a PDF and attaches a broken one must not see the removed
    PDF stay live for students (continuity during a swap is replace_target_pdf's job)."""
    svc.dashboard(STUDENT_A)  # v1 processed + ready
    env.pdfs["thread-bad"] = {1: "no headings here, only prose"}
    env.targets = [{"id": 3, "rag_thread_id": "thread-bad", "original_filename": "bad.pdf"}]
    dash = svc.dashboard(STUDENT_A)
    assert dash["state"] == "failed" and dash["sources"] == []
    assert dash["failed"][0]["filename"] == "bad.pdf"
    sub_v1 = env.db.query(AIGPTopic).filter(AIGPTopic.parent_id.isnot(None)).first()
    with pytest.raises(LMSValidationError):  # cannot keep practising the removed PDF
        svc.start_session(STUDENT_A, sub_v1.id)
    bad = env.db.query(AIGPPdfVersion).filter_by(rag_thread_id="thread-bad").one()
    assert bad.status == "failed" and env.db.query(AIGPTopic).filter_by(version_id=bad.id).count() == 0


def test_pdf_still_ingesting_stays_pending_then_activates(env):
    env.targets = [{"id": 4, "rag_thread_id": "thread-late", "original_filename": "late.pdf"}]
    dash = svc.dashboard(STUDENT_A)
    assert dash["state"] == "processing"
    v = env.db.query(AIGPPdfVersion).filter_by(rag_thread_id="thread-late").one()
    assert v.status == "pending"
    env.pdfs["thread-late"] = PDF_V2
    svc.process_version(v.id, use_llm=False)
    assert svc.dashboard(STUDENT_A)["state"] == "ready"


# -- concurrency -----------------------------------------------------------------------


def test_generation_lock_prevents_duplicate_generation(env):
    sub_id = _sub(svc.dashboard(STUDENT_A), "UNIT 2 Logarithms")["subtopic_id"]
    assert svc._acquire_generation_lock(sub_id)
    # A second request while the lease is held does not generate.
    r = svc.ensure_pool(sub_id, 10)
    assert r == {"generated": 0, "error": None, "waited": True}
    assert env.gen.calls == []
    svc._release_generation_lock(sub_id)
    assert svc.ensure_pool(sub_id, 10)["generated"] == 10
    assert len(env.gen.calls) == 1


def test_near_duplicates_are_rejected():
    a = svc._fingerprint("Solve 2x + 3 = 7 for x", [{"text": "2"}], 0)
    b = svc._fingerprint("Solve 2x + 3 = 7 for x.", [{"text": "2"}], 0)
    c = svc._fingerprint("Solve 5x - 1 = 9 for x", [{"text": "2"}], 0)
    reworded = svc._fingerprint("Find the value of x if 2x + 3 = 7", [{"text": "x = 2"}], 0)
    reworded2 = svc._fingerprint("Find the value of x when 2x + 3 = 7", [{"text": "x = 2"}], 0)
    other_answer = svc._fingerprint("Find the value of x when 2x + 3 = 7", [{"text": "x = 5"}], 0)
    assert svc.is_near_duplicate(b, [a])
    assert not svc.is_near_duplicate(c, [a])
    assert svc.is_near_duplicate(reworded2, [reworded])
    assert not svc.is_near_duplicate(other_answer, [reworded])
    latex_a = svc._fingerprint(
        r"The midpoint of $A(1,2)$ and $B(3,4)$ is",
        [{"text": r"$\left(2, 3\right)$"}],
        0,
    )
    latex_b = svc._fingerprint(
        "The midpoint of A(1,2) and B(3,4) is",
        [{"text": "(2, 3)"}],
        0,
    )
    assert svc.is_near_duplicate(latex_b, [latex_a])


# -- UI wiring (static, same style as the repo's *_static tests) -----------------


def _read(path):
    import pathlib

    return (pathlib.Path(__file__).resolve().parent.parent / path).read_text(encoding="utf-8")


def test_nav_has_independent_ai_guided_practice_tab():
    nav = _read("templates/student/partials/mainnav.html")
    order = [nav.index("'learning-path'"), nav.index("'classes'"), nav.index("'ai-guided-practice'")]
    assert order == sorted(order) and "AI Guided Practice" in nav
    dash = _read("templates/student/dashboard.html")
    assert 'id="view-ai-guided-practice"' in dash and 'id="view-ai-guided-session"' in dash
    assert "student-ai-guided-practice.js" in _read("templates/student/base.html")


def test_ai_guided_practice_is_not_gated_by_diagnostic():
    shell = _read("static/student/js/student-shell.js")
    gated = re.search(r"var GATED = \{([^}]*)\}", shell).group(1)
    assert "ai-guided" not in gated
    assert "'ai-guided-practice'" in re.search(r"var TOP_VIEWS = \[([^\]]*)\]", shell).group(1)


def test_guided_practice_removed_from_learning_path():
    lp = _read("static/student/js/student-learning-path.js")
    assert "openLmsPracticePanel" not in lp and "GUIDED_PRACTICE_ENABLED" not in lp
    assert "openDeficiencyChat" in lp  # Learning Chat button still there


def test_frontend_uses_session_api_and_never_reads_answer_key_before_answer():
    js = _read("static/student/js/student-ai-guided-practice.js")
    for path in ("/ai-guided-practice/dashboard", "/ai-guided-practice/sessions", "/answer", "/complete", "/history"):
        assert path in js
    assert "Retry Practice" in js
    for state_ in ("no_active_pdf", "processing", "failed", "no_topics", "insufficient_content"):
        assert state_ in js


def test_stale_reprocessed_version_never_becomes_active(env):
    """L2: an old, long-replaced version that an admin happened to re-process must
    never be shown to students."""
    env.pdfs["thread-old"] = PDF_V2
    env.targets = [{"id": 5, "rag_thread_id": "thread-old", "original_filename": "old.pdf"}]
    svc.dashboard(STUDENT_A)  # old.pdf active
    env.targets = [{"id": 1, "rag_thread_id": "thread-v1", "original_filename": "target_v1.pdf"}]
    svc.dashboard(STUDENT_A)  # replaced by v1 (now the active one)
    old = env.db.query(AIGPPdfVersion).filter_by(rag_thread_id="thread-old").one()
    old.ready_at = datetime.utcnow() + timedelta(minutes=5)  # e.g. re-processed later by an admin
    env.db.commit()
    env.targets = [{"id": 6, "rag_thread_id": "thread-late", "original_filename": "late.pdf"}]  # still ingesting
    dash = svc.dashboard(STUDENT_A)
    # Neither the removed v1 nor the stale old.pdf is shown: only attached PDFs count.
    assert dash["state"] == "processing" and dash["sources"] == []


# -- QA round 1 regressions (B2, B4, B6, B8) -----------------------------------------


def test_counters_are_recomputed_from_items_not_stale_copies(env):
    """B2: finish/answer recompute counters from the stored answers, so a stale or
    racing writer can never leave a score that disagrees with the answers."""
    sub_id = _sub(svc.dashboard(STUDENT_A), "1.2 Irrational Numbers")["subtopic_id"]
    st = svc.start_session(STUDENT_A, sub_id)
    for item in st["items"][:3]:
        pos = next(o["position"] for o in item["options"] if o["text"].endswith(" correct"))
        svc.submit_answer(STUDENT_A, st["session_id"], item["position"], pos)
    s = env.db.query(AIGPSession).get(st["session_id"])
    s.answered_count, s.correct_count = 0, 0  # what a lost update looked like
    env.db.commit()
    done = svc.complete_session(STUDENT_A, st["session_id"])
    assert done["answered_count"] == 3 and done["correct_count"] == 3
    assert done["score_percent"] == round(100.0 * 3 / done["question_count"], 1)


def test_no_answer_is_recorded_after_finish(env):
    sub_id = _sub(svc.dashboard(STUDENT_A), "1.2 Irrational Numbers")["subtopic_id"]
    st = svc.start_session(STUDENT_A, sub_id)
    svc.complete_session(STUDENT_A, st["session_id"])
    with pytest.raises(LMSValidationError):
        svc.submit_answer(STUDENT_A, st["session_id"], 1, 0)
    items = env.db.query(AIGPSessionItem).filter_by(session_id=st["session_id"]).all()
    assert all(i.answered_at is None for i in items)
    assert svc.complete_session(STUDENT_A, st["session_id"])["score_percent"] == 0.0  # finish twice: stable


def test_request_key_is_bound_to_its_subtopic(env):
    dash = svc.dashboard(STUDENT_A)
    a = _sub(dash, "1.2 Irrational Numbers")["subtopic_id"]
    b = _sub(dash, "UNIT 2 Logarithms")["subtopic_id"]
    svc.start_session(STUDENT_A, a, request_key="same-key")
    with pytest.raises(LMSValidationError):
        svc.start_session(STUDENT_A, b, request_key="same-key")
    with pytest.raises(LMSValidationError):
        svc.start_session(STUDENT_A, b, request_key=12345)


@pytest.mark.parametrize("bad", [10**30, -1, 0, 2**31, 1.5, True, "x", None])
def test_out_of_range_ids_are_not_found_not_500(env, bad):
    svc.dashboard(STUDENT_A)
    with pytest.raises(LMSNotFoundError):
        svc.start_session(STUDENT_A, bad)
    with pytest.raises(LMSNotFoundError):
        svc.session_state(STUDENT_A, bad)


def test_routes_reject_bool_float_and_huge_numbers(env, monkeypatch):
    import app.utils.auth as auth
    from app.routes import lms_routes

    monkeypatch.setattr(auth, "sync_session_user", lambda: True)
    app = Flask("routes2")
    app.secret_key = "t"
    app.register_blueprint(lms_routes.bp, url_prefix="/api/lms")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"], sess["role"] = STUDENT_A, "student"
    svc.dashboard(STUDENT_A)
    for bad in (True, 1.5, 1e30, "abc", None, [1]):
        r = client.post("/api/lms/ai-guided-practice/sessions", json={"subtopic_id": bad})
        assert r.status_code in (400, 404), (bad, r.status_code)
    assert client.get("/api/lms/ai-guided-practice/sessions/99999999999999999999").status_code == 404
    r = client.post("/api/lms/ai-guided-practice/sessions/1/answer", json={"position": True, "option_position": 0})
    assert r.status_code in (400, 404)


def test_raced_duplicate_session_is_removed_not_left_as_history(env, monkeypatch):
    """B4: when another request created an open session for the same subtopic while this
    one was selecting questions, this request's session is deleted (never shown), not
    left behind as 'abandoned' history that marks its questions as seen."""
    sub_id = _sub(svc.dashboard(STUDENT_A), "UNIT 2 Logarithms")["subtopic_id"]
    svc.ensure_pool(sub_id, 10)
    version_id = env.db.query(AIGPTopic).get(sub_id).version_id
    q_racer = env.db.query(AIGPQuestion).filter_by(topic_id=sub_id).first()
    racer = {}
    real_last_sequence = svc._last_sequence

    def racing_last_sequence(student_id, topic_id):
        if not racer:  # the competing request commits first
            r = AIGPSession(student_id=student_id, version_id=version_id, topic_id=topic_id,
                            status="in_progress", question_count=1)
            env.db.add(r)
            env.db.flush()
            env.db.add(AIGPSessionItem(session_id=r.id, question_id=q_racer.id, position=0,
                                       option_order_json="[0,1,2,3]"))
            env.db.commit()
            racer["id"] = r.id
        return real_last_sequence(student_id, topic_id)

    monkeypatch.setattr(svc, "_last_sequence", racing_last_sequence)
    st = svc.start_session(STUDENT_A, sub_id)
    assert st["session_id"] == racer["id"] and st["resumed"]
    assert env.db.query(AIGPSession).filter_by(student_id=STUDENT_A).count() == 1
    assert env.db.query(AIGPSession).filter_by(status="abandoned").count() == 0
    assert set(svc._student_history(STUDENT_A, sub_id)) == {q_racer.id}


# -- QA round 1 regressions (B9, B11, B12) -------------------------------------------


def test_focus_excerpt_matches_the_title_as_a_phrase():
    """B11: 'Real Numbers' must not pull in lines about complex numbers' real part."""
    unit = "\n".join([
        "Real numbers include rationals and irrationals; every real number has a decimal form.",
        "Line two filler.",
        "Line three filler.",
        "Line four filler.",
        "A complex number a+bi has a real part a and the numbers b give the imaginary part.",
    ])
    out = svc.focus_excerpt(unit, "Real Numbers", context_lines=0, min_chars=0)
    assert "decimal form" in out and "complex" not in out


def test_long_sections_rotate_through_windows():
    """B9: a 30k-char section is split into windows; later rounds see later pages."""
    used = [{"page": p, "text": f"PAGE{p} " + "x" * 5000} for p in range(1, 7)]
    windows = svc.source_windows(used, max_chars=12000)
    assert len(windows) == 3
    assert [c["page"] for w in windows for c in w] == [1, 2, 3, 4, 5, 6]
    assert all(sum(len(c["text"]) for c in w) <= 12000 for w in windows)


def test_generation_round_uses_next_window_and_exhaustion_waits_for_all(env, monkeypatch):
    long_pdf = {1: "UNIT 1 Big Unit\n" + " ".join(f"BIGFACT{i} fact {i}." for i in range(5))}
    for p in range(2, 6):
        long_pdf[p] = " ".join(f"BIGFACT{p}{i} fact." for i in range(3)) + " " + "filler " * 1500
    env.pdfs["thread-v1"] = long_pdf
    monkeypatch.setattr(svc, "_MAX_SOURCE_CHARS", 12000)
    sub_id = _sub(svc.dashboard(STUDENT_A), "UNIT 1 Big Unit")["subtopic_id"]
    svc.ensure_pool(sub_id, 40)
    texts = [c["text"] for c in env.gen.calls]
    assert len(texts) == 2 and texts[0] != texts[1]  # two rounds, two different windows
    assert not env.db.query(AIGPTopic).get(sub_id).pool_exhausted


def test_explanation_falls_back_to_verbatim_pdf_evidence(env):
    assert svc._explanation({"explanation": "  Because x.  "}) == "Because x."
    assert svc._explanation({"explanation": None, "grounding_evidence": "D = b^2 - 4ac"}) == (
        'From the Target PDF: "D = b^2 - 4ac"')
    assert svc._explanation({}) is None


# -- Question-bank Target PDFs (DIL SAATHI format; live failure on 2026-11-14) ---------
# Same shape as the server's ingested text: markdown question items carrying a
# "Domain: X | Topic: Y" label line, and an answer key in plain + markdown-table rows.
QBANK = {
    1: "DIL SAATHI Program - Practice Question Bank | Page 1\n# DIL SAATHI PROGRAM\n"
       "| Coverage | Units 1-4 |\n"
       "**Q1. Which number is the additive identity in R?**\n"
       "Domain: Numbers | Topic: Properties of Real Numbers | Cognitive Level: Knowledge | Difficulty: Intermediate\n"
       "A. 1\nB. 0\nC. -1\nD. Undefined\n\n"
       "**Q2. In the radical expression, what is n called?**\n"
       "Domain: Numbers | Topic: Radicals and Radicands | Cognitive Level: Knowledge | Difficulty: Intermediate\n"
       "A. Radicand\nB. Base\nC. Index of the root\nD. Exponent\n"
       "[Table data, extracted with row/column structure]\n| Coverage | Units 1-4 |",
    2: "DIL SAATHI Program - Practice Question Bank   |   Page 2 \n"
       "Q3. Which property is a(b+c)=ab+ac?\n"
       "Domain: Numbers   |   Topic: Properties of Real Numbers   |   Cognitive Level: Understanding\n"
       "A. Associative\nB. Commutative\nC. Distributive\nD. Closure\n"
       "Q4. Factorise x^2-9.\n"
       "Domain: Algebra   |   Topic: Factorization   |   Cognitive Level: Application\n"
       "A. (x-3)(x+3)\nB. (x-9)(x+1)\nC. (x-3)^2\n",
    3: "DIL SAATHI Program - Practice Question Bank   |   Page 3 \nD. x(x-9)\n"
       "Q5. Factorise x^2-4.\n"
       "Domain: Algebra   |   Topic: Factorization   |   Cognitive Level: Application\n"
       "A. (x-2)(x+2)\nB. (x-4)(x+1)\nC. (x-2)^2\nD. x(x-4)\n",
    4: "DIL SAATHI Program - Practice Question Bank   |   Page 4 \nAnswer Key and Assessment Mapping\n"
       "Q Answer Domain Topic Cognitive Level \n1 B Numbers Properties of Real Numbers Knowledge \n"
       "2 C Numbers Radicals and Radicands Knowledge \n3 C Numbers Properties of Real Numbers Understanding \n"
       "| 4 |  |  | A |  |  | Algebra |  |  | Factorization |  |  | Application |\n"
       "| 5 |  |  | A |  |  | Algebra |  |  | Factorization |  |  | Application |",
}


def test_question_bank_labels_become_domain_topic_hierarchy():
    r = topics_mod.extract_hierarchy(_chunks(QBANK), [], [], use_llm=False)
    assert r["structure"] == "question_labels"
    tree = {m["title"]: [s["title"] for s in m["subtopics"]] for m in r["main_topics"]}
    assert tree == {"Numbers": ["Properties of Real Numbers", "Radicals and Radicands"],
                    "Algebra": ["Factorization"]}
    assert all(s["kind"] == "labelled" for m in r["main_topics"] for s in m["subtopics"])


def test_labelled_items_carry_pdf_answer_key_and_no_neighbouring_text():
    pages = topics_mod.page_texts(_chunks(QBANK))
    items = topics_mod.labelled_items(pages, "Numbers", "Properties of Real Numbers")
    assert [i["number"] for i in items] == [1, 3]
    assert items[0]["text"].endswith("Answer: B") and items[1]["text"].endswith("Answer: C")
    assert "Radicand" not in items[0]["text"]  # the next item is not part of this one
    fact = topics_mod.labelled_items(pages, "Algebra", "Factorization")
    assert [i["number"] for i in fact] == [4, 5]
    assert "D. x(x-9)" in fact[0]["text"]  # option continued on the next page
    assert fact[1]["text"].endswith("Answer: A")  # markdown-table answer row
    for i in items + fact:
        for noise in ("Answer Key", "[Table", "| Page", "Q Answer", "Coverage"):
            assert noise not in i["text"], (i["number"], noise)


def test_question_bank_pdf_becomes_practisable_end_to_end(env):
    """The live failure: a grade whose only Target PDF is a question bank showed
    'Topics are not available yet'. It must now show Domain -> Topic and serve
    questions grounded only in that Topic's items."""
    env.pdfs["thread-v1"] = QBANK
    dash = svc.dashboard(STUDENT_A)
    assert dash["state"] == "ready"
    assert [t["title"] for t in dash["sources"][0]["topics"]] == ["Numbers", "Algebra"]
    sub = _sub(dash, "Factorization")
    svc.start_session(STUDENT_A, sub["subtopic_id"])
    text = env.gen.calls[0]["text"]
    assert "Factorise x^2-9" in text and "Factorise x^2-4" in text
    assert "additive identity" not in text and "Radicand" not in text
    assert env.gen.calls[0]["scope"].startswith("Factorization")



def test_explanation_does_not_double_quote_checker_evidence():
    assert svc._explanation({"grounding_evidence": '"Completing the square: x^2 + bx"'}) == (
        'From the Target PDF: "Completing the square: x^2 + bx"')
    assert svc._explanation({"grounding_evidence": "\u201cD = b^2 - 4ac\u201d"}) == 'From the Target PDF: "D = b^2 - 4ac"'


def test_question_bank_explanation_is_labelled_as_related_question():
    out = svc._explanation({"grounding_evidence": "Factorize: 8x^{3} + 27y^{3}"}, "labelled")
    assert out == 'Related question in the Target PDF: "Factorize: 8x^{3} + 27y^{3}"'


def test_old_doubled_quotes_are_cleaned_for_display():
    assert svc._clean_quotes('From the Target PDF: ""Completing the square""') == 'From the Target PDF: "Completing the square"'
    assert svc._clean_quotes(None) is None


# -- Admin-approved Target-bank questions + formatting (live report 2026-11-15) ---------


def _bank(env, statuses):
    """Target-bank document for the QBANK PDF: question number -> (status, text, key)."""
    from app.models.lms_models import Question, TargetBankDocument, TargetBankItem

    doc = TargetBankDocument(sha256="x", original_filename="target_v1.pdf", grade_level="9", assessment_id=50,
                             storage_path="/tmp/x.pdf")
    env.db.add(doc)
    env.db.flush()
    qs = {}
    for number, (status, text, key) in statuses.items():
        q = Question(question_text=text, options_json=json.dumps([{"label": c, "text": f"{text} opt {c}"} for c in "ABCD"]),
                     correct_option_index=key, source_type="pdf_qa_converted", difficulty="medium")
        env.db.add(q)
        env.db.flush()
        env.db.add(TargetBankItem(document_id=doc.id, question_id=q.id, source_question_number=number,
                                  verification_status=status, source_page=number))
        qs[number] = q
    env.db.commit()
    return qs


def test_question_bank_serves_admin_approved_printed_questions_first(env):
    env.pdfs["thread-v1"] = QBANK
    qs = _bank(env, {4: ("approved", "Factorise x^2-9 (admin)", 0), 5: ("needs_review", "Factorise x^2-4 (draft)", 0)})
    sub = _sub(svc.dashboard(STUDENT_A), "Factorization")
    st = svc.start_session(STUDENT_A, sub["subtopic_id"])
    texts = [i["question_text"] for i in st["items"]]
    assert "Factorise x^2-9 (admin)" in texts            # approved printed question served
    assert "Factorise x^2-4 (draft)" not in texts        # not approved by admin -> never served
    bank_item = next(i for i in st["items"] if i["question_text"] == "Factorise x^2-9 (admin)")
    assert bank_item["render_html"] is True               # admin-finalised bank_render formatting
    assert bank_item["position"] is not None


def test_admin_edits_and_rejections_apply_live(env):
    from app.models.lms_models import TargetBankItem

    env.pdfs["thread-v1"] = QBANK
    env.gen.fail = True  # only bank questions in this pool
    qs = _bank(env, {4: ("approved", "Original stem", 0), 5: ("approved", "Second stem", 1)})
    sub_id = _sub(svc.dashboard(STUDENT_A), "Factorization")["subtopic_id"]
    st = svc.start_session(STUDENT_A, sub_id)
    # Admin corrects the text and the key after the session was created.
    qs[4].question_text = "Corrected stem"
    qs[4].correct_option_index = 2
    env.db.commit()
    st = svc.session_state(STUDENT_A, st["session_id"])
    item = next(i for i in st["items"] if i["question_text"] in ("Corrected stem", "Original stem"))
    assert item["question_text"] == "Corrected stem"
    pos_of_key = next(o["position"] for o in item["options"] if o["text"] == "Original stem opt C")
    r = svc.submit_answer(STUDENT_A, st["session_id"], item["position"], pos_of_key)
    assert r["correct"] is True                          # graded against the admin's current key
    # Admin rejects question 5: it leaves the pool for future sessions.
    env.db.query(TargetBankItem).filter_by(source_question_number=5).update({"verification_status": "rejected"})
    env.db.commit()
    assert all(q.bank_question_id != qs[5].id for q in svc._valid_pool(sub_id))


def test_bare_latex_in_prose_is_delimited_for_mathjax():
    d = svc.delimit_bare_latex
    assert d(r"For any real number a, a \times 1 = a") == r"For any real number a, \(a \times 1 = a\)"
    assert d(r"The value of \sqrt{16} is 4.") == r"The value of \(\sqrt{16}\) is 4."
    assert d(r"x = 2 \pm \frac{\sqrt{15}}{3}") == r"\(x = 2 \pm \frac{\sqrt{15}}{3}\)"
    assert d(r"If x \neq 0, then x \div x = 1.") == r"If \(x \neq 0\), then \(x \div x = 1\)."
    # Already-delimited math and plain prose are untouched.
    assert d(r"The factors of $x^{2} - 9$ are:") == r"The factors of $x^{2} - 9$ are:"
    assert d(r"Simplify \(x^{2} - 9\)") == r"Simplify \(x^{2} - 9\)"
    assert d("Common logarithms use base 10.") == "Common logarithms use base 10."


# -- AI tutor chat beside the question --------------------------------------------------


@pytest.fixture()
def tutor(env, monkeypatch):
    from app.services.lms import tutor_service

    calls = []

    def fake_chat(message, api_key="", mode="student", context=None, history=None, solved=False, **k):
        calls.append({"message": message, "context": context, "history": list(history or []), "solved": solved,
                      "review_wrong": k.get("review_wrong", False)})
        return env.tutor_reply_text

    env.tutor_reply_text = "Think about what the PDF says first."
    monkeypatch.setattr(tutor_service, "tutor_chat", fake_chat)
    env.tutor_calls = calls
    return env


def _started(env):
    sub_id = _sub(svc.dashboard(STUDENT_A), "1.2 Irrational Numbers")["subtopic_id"]
    return svc.start_session(STUDENT_A, sub_id)


def test_tutor_guides_without_revealing_answer_before_answering(tutor):
    st = _started(tutor)
    item = st["items"][0]
    key_text = next(o["text"] for o in item["options"] if o["text"].endswith(" correct"))
    tutor.tutor_reply_text = f"The answer is {key_text}."  # model tries to leak the key
    r = svc.tutor_reply(STUDENT_A, st["session_id"], 0, "What is the answer?")
    assert key_text not in r["reply"]  # leak blocked (rewrite also leaked -> guard reply)
    assert tutor.tutor_calls[0]["solved"] is False
    assert "Answer:" not in (tutor.tutor_calls[0]["context"] or "")
    assert len(tutor.tutor_calls) == 2  # one rewrite attempt, like Learning Chat


def test_tutor_is_private_and_validates_input(tutor):
    st = _started(tutor)
    with pytest.raises(LMSNotFoundError):
        svc.tutor_reply(STUDENT_B, st["session_id"], 0, "hi")
    with pytest.raises(LMSValidationError):
        svc.tutor_reply(STUDENT_A, st["session_id"], 0, "   ")
    with pytest.raises(LMSValidationError):
        svc.tutor_reply(STUDENT_A, st["session_id"], 0, "x" * 1001)
    with pytest.raises(LMSNotFoundError):
        svc.tutor_reply(STUDENT_A, st["session_id"], 99, "hi")


def test_tutor_excerpt_never_contains_pdf_answer_keys(env):
    env.pdfs["thread-v1"] = QBANK
    sub = _sub(svc.dashboard(STUDENT_A), "Factorization")
    t = env.db.query(AIGPTopic).get(sub["subtopic_id"])
    main = env.db.query(AIGPTopic).get(t.parent_id)
    v = env.db.query(AIGPPdfVersion).get(t.version_id)
    text = svc._tutor_excerpt(v, main, t)
    assert "Factorise x^2-9" in text and "Answer:" not in text


def test_session_view_has_learning_chat_style_tutor_panel():
    js = _read("static/student/js/student-ai-guided-practice.js")
    for needle in ("sd-split-right", "sd-chat-scroll", "sd-chat-inputbar", "/tutor", "Give me a hint"):
        assert needle in js



def test_learning_chat_tutor_prompt_unchanged_without_review_flag():
    from app.services.lms import tutor_service as ts

    assert "already chose the correct option" in ts.DEFICIENCY_SOLVED_TUTOR_PROMPT
    assert "INCORRECTLY" in ts.DEFICIENCY_REVIEW_WRONG_PROMPT


# -- Learning-Chat style answering: retry until correct (user request 2026-11-15) -----------


def _key_pos(item):
    return next(o["position"] for o in item["options"] if o["text"].endswith(" correct"))


def _wrong_pos(item, n=0):
    return [o["position"] for o in item["options"] if not o["text"].endswith(" correct")][n]


def test_wrong_pick_does_not_reveal_answer_and_student_retries(env):
    sub_id = _sub(svc.dashboard(STUDENT_A), "1.2 Irrational Numbers")["subtopic_id"]
    st = svc.start_session(STUDENT_A, sub_id)
    item = st["items"][0]
    r = svc.submit_answer(STUDENT_A, st["session_id"], 0, _wrong_pos(item))
    assert r["correct"] is False and r["solved"] is False
    for leak in ("correct_position", "correct_label", "explanation", "source_page"):
        assert leak not in r, leak  # the key never leaves the server on a wrong pick
    state = svc.session_state(STUDENT_A, st["session_id"])
    q1 = state["items"][0]
    assert q1["solved"] is False and q1["wrong_positions"] == [_wrong_pos(item)]
    assert "correct_position" not in q1 and "explanation" not in q1
    assert state["current_position"] == 0  # stays on this question
    # Same wrong option again: idempotent; another wrong option: recorded too.
    svc.submit_answer(STUDENT_A, st["session_id"], 0, _wrong_pos(item))
    svc.submit_answer(STUDENT_A, st["session_id"], 0, _wrong_pos(item, 1))
    # Then the correct one: solved, key + explanation now shown, move on.
    r = svc.submit_answer(STUDENT_A, st["session_id"], 0, _key_pos(item))
    assert r["correct"] is True and r["solved"] is True and r["correct_position"] == _key_pos(item)
    state = svc.session_state(STUDENT_A, st["session_id"])
    assert state["items"][0]["solved"] and state["current_position"] == 1
    assert state["items"][0]["first_try_correct"] is False
    with pytest.raises(LMSValidationError):  # cannot change a solved question
        svc.submit_answer(STUDENT_A, st["session_id"], 0, _wrong_pos(item, 2))


def test_score_counts_first_try_and_session_completes_when_all_solved(env):
    sub_id = _sub(svc.dashboard(STUDENT_A), "1.2 Irrational Numbers")["subtopic_id"]
    st = svc.start_session(STUDENT_A, sub_id)
    n = st["question_count"]
    for item in st["items"]:
        if item["position"] == 0:  # one wrong first try, then correct
            svc.submit_answer(STUDENT_A, st["session_id"], 0, _wrong_pos(item))
        r = svc.submit_answer(STUDENT_A, st["session_id"], item["position"], _key_pos(item))
    assert r["session"]["status"] == "completed"
    done = svc.session_state(STUDENT_A, st["session_id"])
    assert done["solved_count"] == n and done["correct_count"] == n - 1 and done["incorrect_count"] == 1
    assert done["score_percent"] == round(100.0 * (n - 1) / n, 1)


def test_session_not_completed_while_a_question_is_unsolved(env):
    sub_id = _sub(svc.dashboard(STUDENT_A), "1.2 Irrational Numbers")["subtopic_id"]
    st = svc.start_session(STUDENT_A, sub_id)
    for item in st["items"]:
        svc.submit_answer(STUDENT_A, st["session_id"], item["position"], _wrong_pos(item))
    state = svc.session_state(STUDENT_A, st["session_id"])
    assert state["status"] == "in_progress" and state["answered_count"] == st["question_count"]
    assert state["solved_count"] == 0


def test_bank_question_note_is_never_shown(env):
    class Q:
        explanation = "This is question 22 of the Target PDF."
    assert svc._shown_explanation(Q) is None
    Q.explanation = "Use the power rule."
    assert svc._shown_explanation(Q) == "Use the power rule."


def test_tutor_gives_hints_until_solved_then_full_solution(tutor):
    st = _started(tutor)
    item = st["items"][0]
    svc.submit_answer(STUDENT_A, st["session_id"], 0, _wrong_pos(item))
    tutor.tutor_reply_text = "Look again at the rule in the PDF."
    svc.tutor_reply(STUDENT_A, st["session_id"], 0, "Why is my answer wrong?")
    call = tutor.tutor_calls[-1]
    assert call["solved"] is False  # still Mode A after a wrong pick: no answer
    assert "(incorrect)" in call["context"]  # tutor knows which option the student tried
    assert "Correct option (use in Mode B" not in call["context"]
    svc.submit_answer(STUDENT_A, st["session_id"], 0, _key_pos(item))
    tutor.tutor_reply_text = "Yes - well done. Here is the full solution: ..."
    r = svc.tutor_reply(STUDENT_A, st["session_id"], 0, "Why is my answer correct?")
    call = tutor.tutor_calls[-1]
    assert call["solved"] is True and call["review_wrong"] is False
    assert "Correct option (use in Mode B" in call["context"]
    assert [m["role"] for m in r["messages"]] == ["user", "assistant", "user", "assistant"]
    state = svc.session_state(STUDENT_A, st["session_id"])
    assert len(state["items"][0]["tutor"]) == 4 and state["items"][1]["tutor"] == []


def test_frontend_retry_until_correct_like_learning_chat():
    js = _read("static/student/js/student-ai-guided-practice.js")
    assert "Take another look, or ask the AI helper for a hint." in js
    assert "The correct answer is" not in js  # never revealed after a wrong pick
    assert "Choose the correct option to continue" in js  # Next locked until solved
    assert "MODE_B_KICKOFF" in js and "Why is my answer correct?" in js
    assert "This is question" not in js



def test_pdf_source_notes_are_never_shown_as_explanations():
    class Q:
        explanation = 'Related question in the Target PDF: "According to the quotient law ..."'
    assert svc._shown_explanation(Q) is None
    Q.explanation = 'From the Target PDF: "Completing the square: ..."'
    assert svc._shown_explanation(Q) is None
