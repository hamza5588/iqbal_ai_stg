"""Re-check (after the test-join fix): diagnostic per-topic correct counts — API results ↔ DB attempt_answers.

    python recheck_diag_topics.py <run_dir>
Uses the stored run: api_dumps/A_diag_results.json + results.json ids, and the live SQLite DB.
"""
import json
import sqlite3
import sys
from pathlib import Path

run = Path(sys.argv[1])
ROOT = Path(__file__).resolve().parents[3]
ids = json.loads((run / "results.json").read_text(encoding="utf-8"))["ctx"]["ids"]
res = json.loads((run / "api_dumps" / "A_diag_results.json").read_text(encoding="utf-8"))["body"]["data"]
con = sqlite3.connect(f"file:{ROOT / 'instance' / 'iqbalai_local.db'}?mode=ro", uri=True)
aid = ids["diag_attempt_A"]
ans = {q: c for q, c in con.execute("select question_id, is_correct from attempt_answers where attempt_id=?", (aid,))}
qids = {t["topic_id"]: t.get("question_ids", []) for t in res["all_topics"]}
out, bad = [], []
for t in res["topic_breakdown"]:
    dbc = sum(1 for q in qids.get(t["topic_id"], []) if ans.get(q))
    dbt = len(qids.get(t["topic_id"], []))
    out.append(f"{t['topic_name']}: API {t['correct']}/{t['total']} DB {dbc}/{dbt}")
    if dbc != t["correct"] or dbt != t["total"]:
        bad.append(out[-1])
scores = {r[0]: r[1] for r in con.execute("select topic_id, score_percent from student_topic_scores where student_id=?", (ids["student_A"],))}
for t in res["topic_breakdown"]:
    s = scores.get(t["topic_id"])
    out.append(f"  StudentTopicScore[{t['topic_id']}]={s} vs diagnostic {t['score_percent']}")
print("\n".join(out))
print("RESULT:", "PASS" if not bad else f"FAIL {bad}")
