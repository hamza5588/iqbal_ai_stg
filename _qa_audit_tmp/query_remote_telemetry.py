#!/usr/bin/env python3
"""Aggregate LLM telemetry. Prints JSON. Never prints DATABASE_URL."""
from __future__ import annotations

import json
import os
from decimal import Decimal

from sqlalchemy import create_engine, text

FALLBACK = {
    ("groq", "llama-3.3-70b-versatile"): (0.59, 0.79),
    ("groq", "llama-3.1-8b-instant"): (0.05, 0.08),
    ("groq", "qwen/qwen3.6-27b"): (0.60, 3.00),
    ("groq", "qwen/qwen3-32b"): (0.29, 0.59),
    ("groq", "mixtral-8x7b-32768"): (0.24, 0.24),
    ("openai", "gpt-4o"): (2.5, 10.0),
    ("openai", "gpt-4o-mini"): (0.15, 0.6),
    ("openai", "gpt-3.5-turbo"): (0.5, 1.5),
}


def to_plain(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            pass
    if isinstance(v, dict):
        return {str(k): to_plain(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [to_plain(x) for x in v]
    if isinstance(v, (int, float, str, bool)):
        return v
    try:
        return dict(v)
    except Exception:
        return str(v)


def rate_for(provider: str, model: str):
    key = ((provider or "").lower(), model or "")
    if key in FALLBACK:
        return FALLBACK[key]
    p = (provider or "").lower()
    m = model or ""
    for (pp, mm), rates in FALLBACK.items():
        if pp == p and mm in m:
            return rates
    return None


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print(json.dumps({"error": "DATABASE_URL missing"}))
        return
    engine = create_engine(url)
    out = {"host": os.environ.get("HOSTNAME") or os.uname().nodename}

    with engine.connect() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        ))]
        out["has_llm_usage_events"] = "llm_usage_events" in tables
        out["has_router_decision_events"] = "router_decision_events" in tables
        out["has_llm_model_pricing"] = "llm_model_pricing" in tables
        out["has_user_token_usage"] = "user_token_usage" in tables

        if "llm_usage_events" not in tables:
            print(json.dumps(out, default=str))
            return

        def rows(sql):
            return [to_plain(dict(r)) for r in c.execute(text(sql)).mappings()]

        def one(sql):
            return to_plain(dict(c.execute(text(sql)).mappings().one()))

        out["coverage"] = one("""
            SELECT MIN(created_at) AS min_at, MAX(created_at) AS max_at, COUNT(*) AS n
            FROM llm_usage_events
        """)

        out["by_traffic"] = rows("""
            SELECT COALESCE(traffic_source,'(null)') AS traffic_source,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN success THEN 1 ELSE 0 END) AS success_calls,
                   SUM(COALESCE(total_tokens,0)) AS total_tokens,
                   SUM(cost_usd) AS cost_usd
            FROM llm_usage_events
            GROUP BY 1
            ORDER BY calls DESC
        """)

        out["token_nulls_prod"] = one("""
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN success THEN 1 ELSE 0 END) AS success_n,
                   SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) AS fail_n,
                   SUM(CASE WHEN input_tokens IS NULL THEN 1 ELSE 0 END) AS null_in,
                   SUM(CASE WHEN output_tokens IS NULL THEN 1 ELSE 0 END) AS null_out,
                   SUM(CASE WHEN total_tokens IS NULL THEN 1 ELSE 0 END) AS null_tot,
                   SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS null_cost,
                   SUM(CASE WHEN cost_usd = 0 THEN 1 ELSE 0 END) AS zero_cost,
                   SUM(COALESCE(input_tokens,0)) AS sum_in,
                   SUM(COALESCE(output_tokens,0)) AS sum_out,
                   SUM(COALESCE(total_tokens,0)) AS sum_tot,
                   SUM(cost_usd) AS sum_cost
            FROM llm_usage_events
            WHERE traffic_source = 'production'
        """)

        out["profile"] = rows("""
            SELECT workflow, user_role, model, provider,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN success THEN 1 ELSE 0 END) AS success_calls,
                   ROUND(AVG(input_tokens)) AS avg_in,
                   ROUND(AVG(output_tokens)) AS avg_out,
                   PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY output_tokens) AS p95_out,
                   SUM(COALESCE(total_tokens,0)) AS total_tokens,
                   SUM(COALESCE(input_tokens,0)) AS input_tokens,
                   SUM(COALESCE(output_tokens,0)) AS output_tokens,
                   SUM(cost_usd) AS cost_usd
            FROM llm_usage_events
            WHERE traffic_source = 'production' AND success = true
            GROUP BY workflow, user_role, model, provider
            ORDER BY calls DESC
        """)

        out["daily"] = rows("""
            SELECT DATE(created_at) AS day,
                   COUNT(*) AS calls,
                   SUM(COALESCE(total_tokens,0)) AS total_tokens,
                   SUM(cost_usd) AS cost_usd
            FROM llm_usage_events
            WHERE traffic_source = 'production' AND success = true
            GROUP BY 1
            ORDER BY 1
        """)

        out["by_workflow"] = rows("""
            SELECT workflow,
                   COUNT(*) AS calls,
                   SUM(COALESCE(input_tokens,0)) AS input_tokens,
                   SUM(COALESCE(output_tokens,0)) AS output_tokens,
                   SUM(COALESCE(total_tokens,0)) AS total_tokens,
                   SUM(cost_usd) AS cost_usd
            FROM llm_usage_events
            WHERE traffic_source = 'production' AND success = true
            GROUP BY 1
            ORDER BY calls DESC
        """)

        out["by_model"] = rows("""
            SELECT provider, model,
                   COUNT(*) AS calls,
                   SUM(COALESCE(input_tokens,0)) AS input_tokens,
                   SUM(COALESCE(output_tokens,0)) AS output_tokens,
                   SUM(COALESCE(total_tokens,0)) AS total_tokens,
                   SUM(cost_usd) AS cost_usd
            FROM llm_usage_events
            WHERE traffic_source = 'production' AND success = true
            GROUP BY 1, 2
            ORDER BY calls DESC
        """)

        if "router_decision_events" in tables:
            out["router_coverage"] = one("""
                SELECT MIN(created_at) AS min_at, MAX(created_at) AS max_at, COUNT(*) AS n
                FROM router_decision_events
            """)
            out["calls_per_turn"] = one("""
                SELECT
                  (SELECT COUNT(*) FROM llm_usage_events
                   WHERE workflow='rag_chat' AND traffic_source='production')::float
                  / NULLIF((SELECT COUNT(*) FROM router_decision_events
                            WHERE traffic_source='production'), 0) AS rag_chat_per_router,
                  (SELECT COUNT(*) FROM llm_usage_events
                   WHERE traffic_source='production' AND success=true)::float
                  / NULLIF((SELECT COUNT(*) FROM router_decision_events
                            WHERE traffic_source='production'), 0) AS all_llm_per_router,
                  (SELECT COUNT(*) FROM llm_usage_events WHERE workflow='rag_chat' AND traffic_source='production') AS rag_chat_n,
                  (SELECT COUNT(*) FROM router_decision_events WHERE traffic_source='production') AS router_n
            """)
            out["router_by_intent"] = rows("""
                SELECT intent, COUNT(*) AS n
                FROM router_decision_events
                WHERE traffic_source='production'
                GROUP BY 1
                ORDER BY n DESC
                LIMIT 20
            """)
        else:
            out["router_coverage"] = None
            out["calls_per_turn"] = None

        if "llm_model_pricing" in tables:
            out["pricing"] = rows("""
                SELECT provider, model, input_usd_per_million, output_usd_per_million
                FROM llm_model_pricing
                ORDER BY provider, model
            """)
        else:
            out["pricing"] = []

        # Estimated USD using fallback rates when DB cost is null/0
        est = 0.0
        missing_rate = []
        for row in out["by_model"]:
            rates = rate_for(row.get("provider") or "", row.get("model") or "")
            inn = float(row.get("input_tokens") or 0)
            outt = float(row.get("output_tokens") or 0)
            if rates:
                ip, op = rates
                row["est_cost_usd"] = (inn * ip + outt * op) / 1_000_000.0
                est += row["est_cost_usd"]
            else:
                row["est_cost_usd"] = None
                missing_rate.append({"provider": row.get("provider"), "model": row.get("model"), "calls": row.get("calls")})
        out["estimated_cost_usd_fallback_rates"] = est
        out["models_missing_fallback_rate"] = missing_rate

        if "user_token_usage" in tables:
            cols = [r[0] for r in c.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='user_token_usage'
            """))]
            out["user_token_usage_columns"] = cols
            # best-effort totals
            try:
                out["user_token_usage_total"] = one(
                    "SELECT COUNT(*) AS n FROM user_token_usage"
                )
            except Exception as e:
                out["user_token_usage_total"] = {"error": str(e)}

    print(json.dumps(to_plain(out), indent=2))


if __name__ == "__main__":
    main()
