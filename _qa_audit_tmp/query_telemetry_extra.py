from __future__ import annotations
import json, os
from decimal import Decimal
from sqlalchemy import create_engine, text

def plain(v):
    if v is None: return None
    if isinstance(v, Decimal): return float(v)
    if hasattr(v, "isoformat"):
        try: return v.isoformat()
        except Exception: pass
    if isinstance(v, dict): return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [plain(x) for x in v]
    if isinstance(v, (int, float, str, bool)): return v
    try: return dict(v)
    except Exception: return str(v)

engine = create_engine(os.environ["DATABASE_URL"])
sql = """
SELECT json_build_object(
  'rag_chat_since_router',
  (SELECT COUNT(*) FROM llm_usage_events
   WHERE workflow='rag_chat' AND traffic_source='production'
     AND created_at >= (SELECT MIN(created_at) FROM router_decision_events)),
  'rag_chat_success_since_router',
  (SELECT COUNT(*) FROM llm_usage_events
   WHERE workflow='rag_chat' AND traffic_source='production' AND success=true
     AND created_at >= (SELECT MIN(created_at) FROM router_decision_events)),
  'router_n',
  (SELECT COUNT(*) FROM router_decision_events WHERE traffic_source='production'),
  'rag_per_router_overlap',
  (SELECT COUNT(*) FROM llm_usage_events
   WHERE workflow='rag_chat' AND traffic_source='production' AND success=true
     AND created_at >= (SELECT MIN(created_at) FROM router_decision_events))::float
  / NULLIF((SELECT COUNT(*) FROM router_decision_events WHERE traffic_source='production'),0),
  'all_llm_per_router_overlap',
  (SELECT COUNT(*) FROM llm_usage_events
   WHERE traffic_source='production' AND success=true
     AND created_at >= (SELECT MIN(created_at) FROM router_decision_events))::float
  / NULLIF((SELECT COUNT(*) FROM router_decision_events WHERE traffic_source='production'),0),
  'heading_since_router',
  (SELECT COUNT(*) FROM llm_usage_events
   WHERE workflow='rag_heading_extraction' AND traffic_source='production' AND success=true
     AND created_at >= (SELECT MIN(created_at) FROM router_decision_events)),
  'distinct_users',
  (SELECT COUNT(DISTINCT user_id) FROM llm_usage_events WHERE traffic_source='production' AND user_id IS NOT NULL),
  'null_user_id',
  (SELECT COUNT(*) FROM llm_usage_events WHERE traffic_source='production' AND user_id IS NULL),
  'utu_sum',
  (SELECT SUM(tokens_used) FROM user_token_usage),
  'avg_cost_per_rag_chat',
  (SELECT AVG(cost_usd) FROM llm_usage_events
   WHERE workflow='rag_chat' AND traffic_source='production' AND success=true),
  'p50_p95_rag',
  (SELECT json_build_object(
     'p50', PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cost_usd),
     'p95', PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cost_usd),
     'avg_in', AVG(input_tokens),
     'avg_out', AVG(output_tokens)
   ) FROM llm_usage_events
   WHERE workflow='rag_chat' AND traffic_source='production' AND success=true),
  'role_split',
  (SELECT json_agg(x) FROM (
     SELECT COALESCE(user_role,'(none)') AS user_role, COUNT(*) calls,
            SUM(cost_usd) cost, SUM(COALESCE(total_tokens,0)) tokens
     FROM llm_usage_events
     WHERE traffic_source='production' AND success=true
     GROUP BY 1
  ) x),
  'fail_by_workflow',
  (SELECT json_agg(x) FROM (
     SELECT workflow, COUNT(*) n FROM llm_usage_events
     WHERE traffic_source='production' AND success=false
     GROUP BY 1 ORDER BY n DESC
  ) x),
  'cost_last_7d',
  (SELECT json_build_object(
     'calls', COUNT(*),
     'tokens', SUM(COALESCE(total_tokens,0)),
     'cost', SUM(cost_usd)
   ) FROM llm_usage_events
   WHERE traffic_source='production' AND success=true
     AND created_at >= NOW() - INTERVAL '7 days'),
  'cost_last_30d',
  (SELECT json_build_object(
     'calls', COUNT(*),
     'tokens', SUM(COALESCE(total_tokens,0)),
     'cost', SUM(cost_usd)
   ) FROM llm_usage_events
   WHERE traffic_source='production' AND success=true
     AND created_at >= NOW() - INTERVAL '30 days')
) AS j
"""
with engine.connect() as c:
    row = c.execute(text(sql)).mappings().one()
print(json.dumps(plain(row["j"]), indent=2))
