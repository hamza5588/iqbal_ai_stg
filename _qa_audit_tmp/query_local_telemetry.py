import json
import sqlite3
from pathlib import Path

DBS = [
    Path(r"c:\Users\user\Desktop\iqbalai-v1.1\iqbal_ai_stg\chatbot.db"),
]
wt = Path(r"c:\Users\user\Desktop\iqbalai-v1.1\iqbal_ai_stg\.claude\worktrees")
if wt.exists():
    for p in wt.glob("*/chatbot.db"):
        DBS.append(p)


def inspect(db_path: Path) -> dict:
    out = {"path": str(db_path), "size": db_path.stat().st_size if db_path.exists() else 0}
    if not db_path.exists():
        out["error"] = "missing"
        return out
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    out["tables_of_interest"] = [t for t in tables if any(x in t.lower() for x in ("llm", "router", "token", "usage"))]
    for t in ["llm_usage_events", "llm_model_pricing", "router_decision_events", "user_token_usage"]:
        if t not in tables:
            out[t] = {"exists": False}
            continue
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        rec = {"exists": True, "count": n}
        if t == "llm_usage_events" and n:
            rec["coverage"] = dict(cur.execute(
                "SELECT MIN(created_at) AS min_at, MAX(created_at) AS max_at, COUNT(*) AS n FROM llm_usage_events"
            ).fetchone())
            rec["by_traffic"] = [dict(r) for r in cur.execute(
                "SELECT traffic_source, COUNT(*) AS n FROM llm_usage_events GROUP BY traffic_source"
            ).fetchall()]
            rec["by_workflow_role_model"] = [dict(r) for r in cur.execute(
                """
                SELECT workflow, user_role, model, provider,
                       COUNT(*) AS calls,
                       SUM(CASE WHEN success=1 OR success='true' THEN 1 ELSE 0 END) AS success_calls,
                       AVG(input_tokens) AS avg_in,
                       AVG(output_tokens) AS avg_out,
                       SUM(COALESCE(total_tokens,0)) AS total_tokens,
                       SUM(COALESCE(cost_usd,0)) AS cost_usd
                FROM llm_usage_events
                GROUP BY workflow, user_role, model, provider
                ORDER BY calls DESC
                """
            ).fetchall()]
            rec["null_tokens"] = dict(cur.execute(
                """
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN input_tokens IS NULL THEN 1 ELSE 0 END) AS null_in,
                       SUM(CASE WHEN output_tokens IS NULL THEN 1 ELSE 0 END) AS null_out,
                       SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS null_cost,
                       SUM(CASE WHEN cost_usd = 0 THEN 1 ELSE 0 END) AS zero_cost
                FROM llm_usage_events
                """
            ).fetchone())
        if t == "llm_model_pricing":
            rec["rows"] = [dict(r) for r in cur.execute("SELECT * FROM llm_model_pricing").fetchall()]
        if t == "router_decision_events" and n:
            rec["coverage"] = dict(cur.execute(
                "SELECT MIN(created_at) AS min_at, MAX(created_at) AS max_at, COUNT(*) AS n FROM router_decision_events"
            ).fetchone())
        out[t] = rec
    con.close()
    return out


results = [inspect(p) for p in DBS]
print(json.dumps(results, default=str, indent=2))
