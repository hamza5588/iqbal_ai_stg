"""One-off cleanup after the Oct 16 2026 answer-key audit. DRY-RUN BY DEFAULT.

What it changes (only with --apply):
  1. Learning Chat: removes still-to-come questions whose answer key was hand-checked
     as wrong / without a correct option (52 fingerprints below) from unfinished
     sessions. Answered history and the session's progress counters are untouched. A
     session left with nothing to do is closed (status completed, as _close_old_sessions
     does), so opening that topic builds a fresh, verified queue.
  2. Question bank: sets is_active = false (rows are kept, nothing is deleted) on
       - every active question in topic 4 "Algebra" (all are QA/load-test leftovers),
       - the "Load test question N" rows,
       - PDF-bank rows with an option that is only a letter ("B") or from the QA test PDF.
     A row that is used by any assessment or has any student answer is never touched.

Safety:
  - Dry-run opens a READ ONLY transaction and prints every row it would change.
  - --apply first writes a JSON backup of every row it will change, then applies all
    changes in one transaction and re-reads them to verify; any mismatch rolls back.
  - --restore BACKUP.json puts the backed-up values back.

Run inside the app container:
  docker exec -i iqbal_ai_stg-flask_app1-1 python scripts/lc_answer_key_cleanup_20261016.py
  docker exec -i iqbal_ai_stg-flask_app1-1 python scripts/lc_answer_key_cleanup_20261016.py --apply \
      --backup-dir /app/tmp/lc_cleanup_backup
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys

from sqlalchemy import bindparam, create_engine, text

# sha1([question_text, [option texts], correct_option_index])[:16] of the 52 Learning Chat
# questions whose key was hand-checked wrong or that have no correct option
# (tests/eval_fixtures/mcq_key_audit_2026_10_16.json, expected wrong_key / no_correct_option).
BAD_QUESTION_FPS = frozenset("""
006118e80f6bf97d 03d3ac9fc766eada 14d26dc135d7c5fa 1df601e78c988eec 2283153677bc65c3
2d6a866659c9e8aa 3d330abab7ffbbed 41d9004b27e3b956 445184673c28ae1c 47e7fab6618bbc8b
47f28b2eb8a82541 4d15d0435988e91a 50941aea3c79453d 56d89e17a0ccc7d2 5737225b239c8f5a
590e79108344347e 5c2b5c112c0bb42d 6298cb69d7611c7c 6796cbe753e0635d 6bdc74dbdf211578
70fe6fefcac674d5 781fbc5bde20d7bf 7dee7ac32d752537 7f19809de6b54b6b 7f3545d06fc1a1a2
83832ee8762ac572 85300201be343535 855cc8ffdb8787c9 88891f5cd182e730 8dbaa54c0795d5ae
93401de937b1d808 9547156b225ce523 98ef4f7d10f24498 a6471d2d774eaa41 a9c8998e3d7b29b6
aa031b06701de8e8 b4de139b47cd7527 b60a22a8156abf64 b88bc68e176f6384 ba4daeb640996edd
bfeca17fc793b997 c70ac750b2bfb90c c7c7e8fd5558fd12 c8768cb658efc21d c8e483d37b4249a5
dd114382f372e59e e905d951222b3bf4 eb4111dbe2d5e39c eba6e073f4471863 f33d23ada094f546
f3e19a75306b180b fead9e06ba771d82
""".split())
assert len(BAD_QUESTION_FPS) == 52

ALGEBRA_TOPIC_ID = 4
LOAD_TEST_IDS = (142, 143, 144, 145)
# PDF-bank rows with a letter-only option or from the QA test PDF (audit 2026-10-16)
PDF_JUNK_IDS = (
    47, 48, 49, 50, 51, 52, 53, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70,
    71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 105, 106, 107, 108, 109, 110,
    111, 130, 131, 133, 134, 135, 136, 137, 166, 168, 169, 170, 172, 173,
)


def fingerprint(q: dict) -> str:
    payload = [q.get("question_text"), [o.get("text") for o in q.get("options") or []], q.get("correct_option_index")]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:16]


def _is_pending(q: dict, pos: int, current_index: int) -> bool:
    """Still in front of the student: not yet reached, or the current one not answered correctly."""
    return pos >= current_index and q.get("correct") is not True


def plan_sessions(conn) -> list:
    rows = conn.execute(text(
        "select id, student_id, status, current_index, correct_count, questions_json "
        "from deficiency_chat_sessions where status in ('active','paused') order by id"
    )).mappings().all()
    plan = []
    for r in rows:
        questions = json.loads(r["questions_json"] or "[]")
        idx = r["current_index"] or 0
        remove = [i for i, q in enumerate(questions) if _is_pending(q, i, idx) and fingerprint(q) in BAD_QUESTION_FPS]
        if not remove:
            continue
        new_questions = [q for i, q in enumerate(questions) if i not in set(remove)]
        plan.append({
            "session_id": r["id"],
            "student_id": r["student_id"],
            "status": r["status"],
            "current_index": idx,
            "questions_before": len(questions),
            "questions_after": len(new_questions),
            "remaining_after": len(new_questions) - idx,
            "new_status": "completed" if len(new_questions) - idx == 0 else r["status"],
            "removed": [{"pos": i, "text": (questions[i].get("question_text") or "")[:90],
                         "answered": bool(questions[i].get("answered"))} for i in remove],
            "old_questions_json": r["questions_json"],
            "new_questions_json": json.dumps(new_questions, ensure_ascii=False),
        })
    return plan


def plan_questions(conn) -> list:
    rows = conn.execute(text(
        "select q.id, q.topic_id, q.source_type, q.is_active, substr(q.question_text, 1, 80) as text, "
        "  (select count(*) from assessment_questions a where a.question_id = q.id) as in_assessments, "
        "  (select count(*) from attempt_answers a where a.question_id = q.id) as student_answers "
        "from questions q where q.is_active and (q.topic_id = :topic or q.id in :ids) order by q.id"
    ).bindparams(bindparam("ids", expanding=True)),
        {"topic": ALGEBRA_TOPIC_ID, "ids": list(LOAD_TEST_IDS) + list(PDF_JUNK_IDS)}).mappings().all()
    plan = []
    for r in rows:
        reason = ("algebra QA test data" if r["topic_id"] == ALGEBRA_TOPIC_ID
                  else "load test question" if r["id"] in LOAD_TEST_IDS else "PDF bank junk")
        plan.append({**dict(r), "reason": reason, "skip": bool(r["in_assessments"] or r["student_answers"])})
    return plan


def show(sessions: list, questions: list) -> None:
    print(f"== Learning Chat sessions to clean: {len(sessions)}, "
          f"questions removed: {sum(len(s['removed']) for s in sessions)}")
    for s in sessions:
        print(f"  session {s['session_id']} student {s['student_id']} {s['status']} at {s['current_index']}: "
              f"{s['questions_before']} -> {s['questions_after']} questions ({s['remaining_after']} still to do)"
              + (f"; status {s['status']} -> {s['new_status']}" if s["new_status"] != s["status"] else ""))
        for q in s["removed"]:
            print(f"     - pos {q['pos']}{' (answered wrong, current)' if q['answered'] else ''}: {q['text']}")
    change = [q for q in questions if not q["skip"]]
    print(f"== Questions to deactivate (is_active=false): {len(change)}; skipped (in use): "
          f"{[q['id'] for q in questions if q['skip']]}")
    by_reason: dict = {}
    for q in change:
        by_reason.setdefault(q["reason"], []).append(q["id"])
    for reason, ids in by_reason.items():
        print(f"  {reason}: {len(ids)} -> {ids}")
    for q in change:
        print(f"     {q['id']:>4} [{q['source_type']}] {q['text']!r}")


def apply(engine, backup_dir: str) -> str:
    with engine.begin() as conn:
        sessions, questions = plan_sessions(conn), plan_questions(conn)
        change = [q for q in questions if not q["skip"]]
        show(sessions, questions)
        if any(s["remaining_after"] < 0 for s in sessions):
            raise SystemExit("refusing: a session would end up before its own current_index")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        path = os.path.join(backup_dir, f"lc_answer_key_cleanup_{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "sessions": [{"id": s["session_id"], "questions_json": s["old_questions_json"], "status": s["status"]}
                             for s in sessions],
                "questions": [{"id": q["id"], "is_active": True} for q in change],
            }, f, ensure_ascii=False)
        print(f"== backup written: {path}")
        for s in sessions:
            done = conn.execute(
                text("update deficiency_chat_sessions set questions_json = :j, status = :st "
                     "where id = :id and questions_json = :old and status = :old_st"),
                {"j": s["new_questions_json"], "st": s["new_status"], "id": s["session_id"],
                 "old": s["old_questions_json"], "old_st": s["status"]},
            )
            if done.rowcount != 1:
                raise SystemExit(f"session {s['session_id']} changed while running; rolled back")
        if change:
            conn.execute(text("update questions set is_active = false where id in :ids and is_active")
                         .bindparams(bindparam("ids", expanding=True)), {"ids": [q["id"] for q in change]})
        # verify inside the same transaction; any mismatch rolls everything back
        for s in sessions:
            now = conn.execute(text("select questions_json from deficiency_chat_sessions where id = :id"),
                               {"id": s["session_id"]}).scalar()
            if now != s["new_questions_json"]:
                raise SystemExit(f"verification failed for session {s['session_id']}; rolled back")
        still = conn.execute(text("select count(*) from questions where id in :ids and is_active")
                             .bindparams(bindparam("ids", expanding=True)),
                             {"ids": [q["id"] for q in change] or [0]}).scalar()
        if still:
            raise SystemExit(f"verification failed: {still} question(s) still active; rolled back")
        left = sum(1 for s in plan_sessions(conn) for _ in s["removed"])
        if left:
            raise SystemExit(f"verification failed: {left} bad question(s) still queued; rolled back")
    print("== applied and verified")
    return path


def restore(engine, path: str) -> None:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    with engine.begin() as conn:
        for s in data["sessions"]:
            conn.execute(text("update deficiency_chat_sessions set questions_json = :j, status = :st where id = :id"),
                         {"j": s["questions_json"], "st": s["status"], "id": s["id"]})
        ids = [q["id"] for q in data["questions"]]
        if ids:
            conn.execute(text("update questions set is_active = true where id in :ids")
                         .bindparams(bindparam("ids", expanding=True)), {"ids": ids})
    print(f"== restored {len(data['sessions'])} session(s) and {len(data['questions'])} question(s) from {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    ap.add_argument("--backup-dir", default="/app/tmp/lc_cleanup_backup")
    ap.add_argument("--restore", metavar="BACKUP_JSON")
    args = ap.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    if args.restore:
        restore(engine, args.restore)
    elif args.apply:
        apply(engine, args.backup_dir)
    else:
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                conn.execute(text("set transaction read only"))
            show(plan_sessions(conn), plan_questions(conn))
            conn.rollback()
        print("\n(dry run - nothing was changed; re-run with --apply to make these changes)")


if __name__ == "__main__":
    sys.exit(main())
