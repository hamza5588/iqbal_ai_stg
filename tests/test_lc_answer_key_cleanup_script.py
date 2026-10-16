"""The one-off Oct 2026 DB cleanup: dry run changes nothing, --apply backs up, changes
exactly the planned rows and verifies, --restore undoes it."""
import importlib.util
import json
import pathlib

import pytest
from sqlalchemy import create_engine, text

from app.models.lms_models import AssessmentQuestion, AttemptAnswer, Base, DeficiencyChatSession, Question, Topic

ROOT = pathlib.Path(__file__).resolve().parents[1]
AUDIT = json.loads((ROOT / "tests/eval_fixtures/mcq_key_audit_2026_10_16.json").read_text(encoding="utf-8"))["items"]


def _load_script():
    spec = importlib.util.spec_from_file_location("cleanup", ROOT / "scripts/lc_answer_key_cleanup_20261016.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cleanup = _load_script()
BAD = next(i for i in AUDIT if i["expected"] == "no_correct_option")
GOOD = next(i for i in AUDIT if i["expected"] == "ok")


def _queue_item(src, **state):
    return {"question_text": src["question_text"], "options": src["options"],
            "correct_option_index": src["correct_option_index"], "answered": False, "correct": None, **state}


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    Base.metadata.create_all(eng, tables=[t.__table__ for t in (Topic, Question, AssessmentQuestion, AttemptAnswer, DeficiencyChatSession)])
    opts = json.dumps([{"label": l, "text": l} for l in "ABCD"])
    with eng.begin() as c:
        for qid, topic, src in [(336, 4, "manual"), (142, None, "manual"), (47, None, "pdf_qa_converted"),
                                (48, None, "pdf_qa_converted"), (557, None, "pdf_qa_converted")]:
            c.execute(text("insert into questions (id, topic_id, question_text, options_json, correct_option_index, "
                           "difficulty, source_type, is_active) values (:i, :t, 'q', :o, 0, 'medium', :s, 1)"),
                      {"i": qid, "t": topic, "o": opts, "s": src})
        c.execute(text("insert into assessment_questions (assessment_id, question_id, sort_order) values (3, 48, 0)"))
        sessions = [
            (1, "active", 1, [_queue_item(BAD, answered=True, correct=False), _queue_item(BAD), _queue_item(GOOD)]),
            (2, "paused", 0, [_queue_item(BAD)]),
            (3, "completed", 0, [_queue_item(BAD)]),  # finished sessions are history: untouched
        ]
        for sid, status, idx, qs in sessions:
            c.execute(text("insert into deficiency_chat_sessions (id, student_id, status, current_index, correct_count, "
                           "questions_json) values (:i, 7, :s, :x, 0, :q)"),
                      {"i": sid, "s": status, "x": idx, "q": json.dumps(qs, ensure_ascii=False)})
    return eng


def _snapshot(eng):
    with eng.connect() as c:
        return (
            c.execute(text("select id, is_active from questions order by id")).all(),
            c.execute(text("select id, status, questions_json from deficiency_chat_sessions order by id")).all(),
        )


def test_fingerprints_match_the_audit_fixture():
    bad = {cleanup.fingerprint(i) for i in AUDIT if i["expected"] in ("wrong_key", "no_correct_option")}
    assert bad == cleanup.BAD_QUESTION_FPS


def test_plan_lists_exactly_the_pending_bad_questions_and_unused_junk(engine):
    with engine.connect() as c:
        sessions, questions = cleanup.plan_sessions(c), cleanup.plan_questions(c)
    assert [(s["session_id"], [r["pos"] for r in s["removed"]], s["new_status"]) for s in sessions] == [
        (1, [1], "active"),   # pos 0 is answered history (before current_index); pos 1 is still to come
        (2, [0], "completed"),  # nothing left to do -> closed
    ]
    assert {q["id"]: q["skip"] for q in questions} == {47: False, 48: True, 142: False, 336: False}


def test_dry_run_changes_nothing(engine, monkeypatch, capsys):
    before = _snapshot(engine)
    monkeypatch.setattr(cleanup, "create_engine", lambda url: engine)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setattr("sys.argv", ["cleanup"])
    cleanup.main()
    assert "dry run - nothing was changed" in capsys.readouterr().out
    assert _snapshot(engine) == before


def test_apply_backs_up_changes_verifies_and_restore_undoes_it(engine, tmp_path):
    before = _snapshot(engine)
    backup = cleanup.apply(engine, str(tmp_path / "bk"))
    with engine.connect() as c:
        active = dict(c.execute(text("select id, is_active from questions")).all())
        s1 = json.loads(c.execute(text("select questions_json from deficiency_chat_sessions where id = 1")).scalar())
        st = dict(c.execute(text("select id, status from deficiency_chat_sessions")).all())
    assert active == {47: 0, 48: 1, 142: 0, 336: 0, 557: 1}
    assert [q["question_text"] for q in s1] == [BAD["question_text"], GOOD["question_text"]]
    assert st == {1: "active", 2: "completed", 3: "completed"}
    saved = json.loads(pathlib.Path(backup).read_text(encoding="utf-8"))
    assert {s["id"] for s in saved["sessions"]} == {1, 2}
    assert {q["id"] for q in saved["questions"]} == {47, 142, 336}

    cleanup.restore(engine, backup)
    assert _snapshot(engine) == before


def test_apply_twice_is_a_no_op(engine, tmp_path):
    cleanup.apply(engine, str(tmp_path / "bk"))
    after = _snapshot(engine)
    cleanup.apply(engine, str(tmp_path / "bk"))
    assert _snapshot(engine) == after
