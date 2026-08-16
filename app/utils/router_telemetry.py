"""
Phase 4: structured tracing for turn-intent routing decisions.

Sibling to app/utils/llm_gateway.py (kept separate rather than folded in - routing-decision
tracing and per-LLM-call cost/latency telemetry are different concerns someone may want to
toggle independently). Reuses llm_gateway's existing LlmTelemetryContext ContextVar for actor
context (user/role/traffic_source/workflow/conversation/thread) rather than inventing a new one,
since router decisions always happen inside the same Flask request/Celery task as the LLM calls
already tracked there.

See PHASE4_DESIGN.md section 1 for the schema rationale (why a new RouterDecisionEvent table
rather than new columns on LLMUsageEvent).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from app.utils.llm_gateway import get_llm_telemetry_context

logger = logging.getLogger(__name__)

ROUTER_DECISION_TRACING_ENABLED_ENV = "ROUTER_DECISION_TRACING_ENABLED"


def _router_tracing_enabled() -> bool:
    return os.getenv(ROUTER_DECISION_TRACING_ENABLED_ENV, "true").lower() in ("true", "1", "yes")


def _truncate(text: Optional[str], n: int = 2000) -> Optional[str]:
    if not text:
        return None
    text = str(text)
    return text if len(text) <= n else text[:n] + "…"


def persist_router_decision_event(
    *,
    router_output: Any,
    router_used_fallback: bool = False,
    fallback_reason: Optional[str] = None,
    router_llm_usage_event_id: Optional[int] = None,
    prefetch_branch: Optional[str] = None,
    meta_conversation_active: bool = False,
    own_answer_followup_active: bool = False,
    tool_rounds_used: Optional[int] = None,
    tool_round_limit_reached: bool = False,
    outcome: str = "success",
    error_class: Optional[str] = None,
    error_message: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> None:
    """
    Persist one RouterDecisionEvent row for the current turn.

    `router_output` is expected to be a RouterOutput instance (or any object exposing the same
    `intent` / `requested_brevity` / `meta_conversation_scope` / `meta_conversation_n` /
    `reasoning` attributes) - accessed via getattr with defaults so this module has no hard
    import dependency on rag_service.py's RouterOutput class (avoids a circular import, since
    rag_service.py is the caller).

    Never raises: failures are logged and swallowed, exactly like
    llm_gateway.persist_llm_usage_event, so a tracing failure can never break a chat turn.
    """
    if not _router_tracing_enabled():
        return

    ctx = get_llm_telemetry_context()

    try:
        from app.utils.db import get_db
        from app.models.database_models import RouterDecisionEvent

        db = get_db()
        ev = RouterDecisionEvent(
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            traffic_source=ctx.traffic_source or "production",
            workflow=ctx.workflow or "unknown",
            conversation_id=ctx.conversation_id,
            thread_id=(ctx.thread_id or "")[:255] if ctx.thread_id else None,
            router_llm_usage_event_id=router_llm_usage_event_id,
            intent=(getattr(router_output, "intent", None) or None),
            requested_brevity=getattr(router_output, "requested_brevity", None),
            meta_conversation_scope=getattr(router_output, "meta_conversation_scope", None),
            meta_conversation_n=getattr(router_output, "meta_conversation_n", None),
            reasoning=_truncate(getattr(router_output, "reasoning", None)),
            router_used_fallback=bool(router_used_fallback),
            fallback_reason=(fallback_reason or "")[:255] if fallback_reason else None,
            prefetch_branch=(prefetch_branch or "")[:64] if prefetch_branch else None,
            meta_conversation_active=bool(meta_conversation_active),
            own_answer_followup_active=bool(own_answer_followup_active),
            tool_rounds_used=tool_rounds_used,
            tool_round_limit_reached=bool(tool_round_limit_reached),
            outcome=(outcome or "success")[:16],
            error_class=(error_class or "")[:255] if error_class else None,
            error_message=_truncate(error_message),
            duration_ms=max(0, int(duration_ms)) if duration_ms is not None else None,
        )
        db.add(ev)
        db.commit()
    except Exception as e:
        logger.warning("Failed to persist router decision event: %s", e, exc_info=True)
        try:
            from app.utils.db import get_db

            get_db().rollback()
        except Exception:
            pass
