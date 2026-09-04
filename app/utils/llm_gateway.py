"""
Centralized LLM telemetry: every chat completion goes through TelemetryChatModel
(see llm_factory.create_llm) so we can record tokens, cost, latency, workflow, and actor.
"""
from __future__ import annotations

import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, BaseMessageChunk
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from pydantic import ConfigDict, Field
from typing_extensions import override

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request / task context (set at Flask before_request, route handlers, Celery)
# ---------------------------------------------------------------------------


@dataclass
class LlmTelemetryContext:
    user_id: Optional[int] = None
    user_role: Optional[str] = None
    workflow: str = "unknown"
    traffic_source: str = "production"
    conversation_id: Optional[int] = None
    thread_id: Optional[str] = None
    celery_task_name: Optional[str] = None


_llm_telemetry_ctx: ContextVar[Optional[LlmTelemetryContext]] = ContextVar(
    "llm_telemetry_ctx", default=None
)


def get_llm_telemetry_context() -> LlmTelemetryContext:
    c = _llm_telemetry_ctx.get()
    if c is None:
        return LlmTelemetryContext()
    return c


def set_llm_telemetry_context(ctx: LlmTelemetryContext) -> Any:
    """Returns the ContextVar token for use with reset()."""
    return _llm_telemetry_ctx.set(ctx)


def reset_llm_telemetry_context(token: Any) -> None:
    _llm_telemetry_ctx.reset(token)


def update_llm_telemetry_context(**kwargs: Any) -> None:
    """Merge fields into the current context (or create one)."""
    old = _llm_telemetry_ctx.get()
    cur = old if old is not None else LlmTelemetryContext()
    for f in fields(LlmTelemetryContext):
        if f.name in kwargs:
            setattr(cur, f.name, kwargs[f.name])
    _llm_telemetry_ctx.set(cur)


def resolve_traffic_source_for_request() -> str:
    """Production vs load_test: env, optional header, load-test routes."""
    if os.getenv("LOAD_TEST_MODE", "false").lower() in ("true", "1", "yes"):
        return "load_test"
    try:
        from flask import has_request_context, request

        if has_request_context():
            if (request.headers.get("X-Load-Test") or "").strip() in ("1", "true", "yes"):
                return "load_test"
            p = (request.path or "").lower()
            if "/api/load-test" in p or p.startswith("/api/load-test"):
                return "load_test"
    except Exception:
        pass
    return "production"


# ---------------------------------------------------------------------------
# Token extraction (LangChain + provider quirks)
# ---------------------------------------------------------------------------


def extract_token_counts_from_chat_result(result: ChatResult) -> tuple[Optional[int], Optional[int], Optional[int]]:
    if not result or not result.generations:
        return None, None, None
    gen0 = result.generations[0]
    if isinstance(gen0, ChatGeneration):
        msg = gen0.message
        return extract_token_counts_from_message(msg)
    return None, None, None


def extract_token_counts_from_message(msg: BaseMessage) -> tuple[Optional[int], Optional[int], Optional[int]]:
    inp = out = tot = None
    um = getattr(msg, "usage_metadata", None)
    if isinstance(um, dict):
        inp = um.get("input_tokens")
        if inp is None:
            inp = um.get("prompt_tokens")
        out = um.get("output_tokens")
        if out is None:
            out = um.get("completion_tokens")
        tot = um.get("total_tokens")
    meta = getattr(msg, "response_metadata", None) or {}
    if isinstance(meta, dict):
        tu = meta.get("token_usage")
        if isinstance(tu, dict):
            if inp is None:
                inp = tu.get("prompt_tokens")
            if out is None:
                out = tu.get("completion_tokens")
            if tot is None:
                tot = tu.get("total_tokens")
        u2 = meta.get("usage")
        if isinstance(u2, dict):
            if inp is None:
                inp = u2.get("prompt_tokens") or u2.get("input_tokens")
            if out is None:
                out = u2.get("completion_tokens") or u2.get("output_tokens")
            if tot is None:
                tot = u2.get("total_tokens")
    if tot is None and inp is not None and out is not None:
        tot = inp + out
    return inp, out, tot


def extract_token_counts_from_chunk(chunk: BaseMessageChunk) -> tuple[Optional[int], Optional[int], Optional[int]]:
    um = getattr(chunk, "usage_metadata", None)
    if isinstance(um, dict):
        inp = um.get("input_tokens") or um.get("prompt_tokens")
        out = um.get("output_tokens") or um.get("completion_tokens")
        tot = um.get("total_tokens")
        if tot is None and inp is not None and out is not None:
            tot = inp + out
        return inp, out, tot
    meta = getattr(chunk, "response_metadata", None) or {}
    if isinstance(meta, dict) and meta.get("token_usage"):
        tu = meta["token_usage"]
        if isinstance(tu, dict):
            inp = tu.get("prompt_tokens")
            out = tu.get("completion_tokens")
            tot = tu.get("total_tokens")
            return inp, out, tot
    return None, None, None


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

# Fallback $/1M when DB has no row (approximate; admin should set llm_model_pricing).
_DEFAULT_MODEL_PRICING: dict[tuple[str, str], tuple[float, float]] = {
    # Every LLM_PROVIDER=groq request currently runs on this model and it was
    # missing from both this table and llm_model_pricing (DB) - every telemetry
    # row was getting cost_usd=NULL and the admin LLM Telemetry dashboard showed
    # $0 across the board. DB row (app/routes/admin_routes.py pricing endpoint)
    # takes precedence over this fallback; keep this in sync as a safety net.
    ("groq", "openai/gpt-oss-120b"): (0.15, 0.60),
    ("groq", "llama-3.3-70b-versatile"): (0.59, 0.79),
    ("groq", "llama-3.1-8b-instant"): (0.05, 0.08),
    ("groq", "qwen/qwen3.6-27b"): (0.60, 3.00),
    ("groq", "qwen/qwen3-32b"): (0.29, 0.59),  # retired on Groq; kept for historical telemetry
    ("groq", "mixtral-8x7b-32768"): (0.24, 0.24),
    ("openai", "gpt-4o"): (2.5, 10.0),
    ("openai", "gpt-4o-mini"): (0.15, 0.6),
    ("openai", "gpt-3.5-turbo"): (0.5, 1.5),
    ("vllm", "*"): (0.0, 0.0),
}


def compute_cost_usd(
    provider: str,
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> Optional[Decimal]:
    if input_tokens is None and output_tokens is None:
        return None
    it = input_tokens or 0
    ot = output_tokens or 0
    try:
        from app.utils.db import get_db
        from app.models.database_models import LLMModelPricing

        db = get_db()
        row = (
            db.query(LLMModelPricing)
            .filter(
                LLMModelPricing.provider == provider.lower(),
                LLMModelPricing.model == model,
            )
            .first()
        )
        if row:
            ip = float(row.input_usd_per_million or 0)
            op = float(row.output_usd_per_million or 0)
            return Decimal(str((it * ip + ot * op) / 1_000_000.0))
    except Exception as e:
        logger.debug("LLM pricing DB lookup failed: %s", e)

    key = (provider.lower(), model)
    pair = _DEFAULT_MODEL_PRICING.get(key)
    if pair is None:
        ip, op = None, None
    else:
        ip, op = pair
    if ip is None and provider.lower() == "vllm":
        pair_v = _DEFAULT_MODEL_PRICING.get(("vllm", "*"), (0.0, 0.0))
        ip, op = pair_v
    if ip is None:
        # Try normalized groq model id prefix
        for (p, m), v in _DEFAULT_MODEL_PRICING.items():
            if p == provider.lower() and m in (model or ""):
                ip, op = v
                break
    if ip is None:
        return None
    return Decimal(str((it * ip + ot * op) / 1_000_000.0))


# ---------------------------------------------------------------------------
# Persistence + optional dual-write to UserTokenUsage
# ---------------------------------------------------------------------------


def _truncate_err(msg: str, n: int = 2000) -> str:
    if not msg:
        return ""
    return msg if len(msg) <= n else msg[:n] + "…"


def persist_llm_usage_event(
    *,
    provider: str,
    model: str,
    duration_ms: int,
    success: bool,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    total_tokens: Optional[int],
    error_class: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    if os.getenv("LLM_TELEMETRY_ENABLED", "true").lower() not in ("true", "1", "yes"):
        return

    ctx = get_llm_telemetry_context()
    cost = compute_cost_usd(provider, model, input_tokens, output_tokens)

    try:
        from app.utils.db import get_db
        from app.models.database_models import LLMUsageEvent

        db = get_db()
        ev = LLMUsageEvent(
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            traffic_source=ctx.traffic_source or "production",
            workflow=ctx.workflow or "unknown",
            provider=provider[:64],
            model=(model or "")[:255],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            duration_ms=max(0, int(duration_ms)),
            success=success,
            error_class=(error_class or "")[:255] if error_class else None,
            error_message=_truncate_err(error_message or "") or None,
            conversation_id=ctx.conversation_id,
            thread_id=(ctx.thread_id or "")[:255] if ctx.thread_id else None,
            celery_task_name=(ctx.celery_task_name or "")[:255] if ctx.celery_task_name else None,
        )
        db.add(ev)
        db.commit()
    except Exception as e:
        logger.warning("Failed to persist LLM usage event: %s", e, exc_info=True)
        try:
            from app.utils.db import get_db

            get_db().rollback()
        except Exception:
            pass

    if (
        success
        and ctx.user_id
        and total_tokens
        and int(total_tokens) > 0
        and os.getenv("LLM_TELEMETRY_DUAL_WRITE_USER_TOKEN_USAGE", "true").lower()
        in ("true", "1", "yes")
    ):
        try:
            from app.utils.db import update_token_usage

            update_token_usage(int(ctx.user_id), int(total_tokens))
        except Exception as e:
            logger.debug("Dual-write token usage skipped: %s", e)


# ---------------------------------------------------------------------------
# Telemetry-wrapped chat model
# ---------------------------------------------------------------------------


class TelemetryChatModel(BaseChatModel):
    """Delegates to an inner BaseChatModel and records one row per completion."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    bound: BaseChatModel = Field(exclude=True)
    telemetry_provider: str = Field(default="unknown")
    telemetry_model: str = Field(default="unknown")

    @property
    def _llm_type(self) -> str:
        return "telemetry_wrapped"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        try:
            base = dict(self.bound._identifying_params)  # type: ignore[attr-defined]
        except Exception:
            base = {}
        base["telemetry_provider"] = self.telemetry_provider
        base["telemetry_model"] = self.telemetry_model
        return base

    def _record_success(self, result: ChatResult, duration_ms: float) -> None:
        inp, out, tot = extract_token_counts_from_chat_result(result)
        if tot is None and result.llm_output and isinstance(result.llm_output, dict):
            tok = result.llm_output.get("token_usage") or result.llm_output.get("usage")
            if isinstance(tok, dict):
                if inp is None:
                    inp = tok.get("prompt_tokens")
                if out is None:
                    out = tok.get("completion_tokens")
                if tot is None:
                    tot = tok.get("total_tokens")
        persist_llm_usage_event(
            provider=self.telemetry_provider,
            model=self.telemetry_model,
            duration_ms=int(duration_ms),
            success=True,
            input_tokens=inp,
            output_tokens=out,
            total_tokens=tot,
        )

    def _record_failure(self, duration_ms: float, exc: BaseException) -> None:
        persist_llm_usage_event(
            provider=self.telemetry_provider,
            model=self.telemetry_model,
            duration_ms=int(duration_ms),
            success=False,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            error_class=type(exc).__name__,
            error_message=str(exc),
        )

    def _telemetry_record_tool_or_structured_output(
        self,
        out: Any,
        duration_ms: float,
        exc: Optional[BaseException],
    ) -> None:
        """Record usage for bind_tools / Runnable paths (not _generate)."""
        if exc is not None:
            persist_llm_usage_event(
                provider=self.telemetry_provider,
                model=self.telemetry_model,
                duration_ms=int(duration_ms),
                success=False,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                error_class=type(exc).__name__,
                error_message=str(exc),
            )
            return
        inp_t = out_t = tot_t = None
        if isinstance(out, AIMessage):
            inp_t, out_t, tot_t = extract_token_counts_from_message(out)
        elif isinstance(out, dict):
            raw = out.get("raw")
            if isinstance(raw, AIMessage):
                inp_t, out_t, tot_t = extract_token_counts_from_message(raw)
        persist_llm_usage_event(
            provider=self.telemetry_provider,
            model=self.telemetry_model,
            duration_ms=int(duration_ms),
            success=True,
            input_tokens=inp_t,
            output_tokens=out_t,
            total_tokens=tot_t,
        )

    def bind_tools(  # type: ignore[override]
        self,
        tools: Any,
        **kwargs: Any,
    ) -> Runnable:
        """Delegate to the wrapped model and record telemetry on each invoke/ainvoke."""
        inner = self.bound.bind_tools(tools, **kwargs)

        def _sync_invoke(input: Any, config: Optional[RunnableConfig] = None, **kw: Any) -> Any:
            t0 = time.perf_counter()
            try:
                out = inner.invoke(input, config=config, **kw)
                self._telemetry_record_tool_or_structured_output(
                    out, (time.perf_counter() - t0) * 1000, None
                )
                return out
            except Exception as e:
                self._telemetry_record_tool_or_structured_output(
                    None, (time.perf_counter() - t0) * 1000, e
                )
                raise

        async def _async_invoke(input: Any, config: Optional[RunnableConfig] = None, **kw: Any) -> Any:
            t0 = time.perf_counter()
            try:
                out = await inner.ainvoke(input, config=config, **kw)
                self._telemetry_record_tool_or_structured_output(
                    out, (time.perf_counter() - t0) * 1000, None
                )
                return out
            except Exception as e:
                self._telemetry_record_tool_or_structured_output(
                    None, (time.perf_counter() - t0) * 1000, e
                )
                raise

        return RunnableLambda(_sync_invoke, afunc=_async_invoke)

    @override
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        t0 = time.perf_counter()
        try:
            result = self.bound._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            self._record_success(result, (time.perf_counter() - t0) * 1000)
            return result
        except Exception as e:
            self._record_failure((time.perf_counter() - t0) * 1000, e)
            raise

    @override
    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        t0 = time.perf_counter()
        try:
            agen = self.bound._agenerate  # type: ignore[attr-defined]
            result = await agen(messages, stop=stop, run_manager=run_manager, **kwargs)
            self._record_success(result, (time.perf_counter() - t0) * 1000)
            return result
        except Exception as e:
            self._record_failure((time.perf_counter() - t0) * 1000, e)
            raise

    # Do not override _stream/_astream: when the bound model has no streaming, stream()
    # falls back to invoke() -> _generate (tracked). When streaming is used, v1 may miss
    # per-chunk usage unless providers attach usage to the final chunk.


def wrap_llm_with_telemetry(
    llm: BaseChatModel,
    *,
    provider: str,
    model_name: str,
) -> BaseChatModel:
    if os.getenv("LLM_TELEMETRY_ENABLED", "true").lower() not in ("true", "1", "yes"):
        return llm
    return TelemetryChatModel(
        bound=llm,
        telemetry_provider=provider or "unknown",
        telemetry_model=model_name or "unknown",
    )


def init_flask_request_llm_telemetry() -> None:
    """Register on Flask before_request: user, role, traffic_source, coarse workflow."""
    try:
        from flask import g, request, session

        user_id = session.get("user_id")
        role = session.get("role")
        ts = resolve_traffic_source_for_request()
        ep = (request.endpoint or "") or ""
        wf = "unknown"
        if ep == "rag.chat":
            wf = "rag_chat"
        elif ep.startswith("rag."):
            wf = "rag_api"
        elif ep.startswith("lesson_routes.") or "lesson" in ep:
            wf = "lesson_api"
        elif ep.startswith("chat.") or ep.startswith("stt."):
            wf = "legacy_chat"
        elif "chatbot" in ep:
            wf = "chatbot"
        elif ep.startswith("admin."):
            wf = "admin"
        ctx = LlmTelemetryContext(
            user_id=user_id,
            user_role=role,
            workflow=wf,
            traffic_source=ts,
        )
        g._llm_telemetry_token = set_llm_telemetry_context(ctx)
    except Exception as e:
        logger.debug("init_flask_request_llm_telemetry skipped: %s", e)


def teardown_flask_request_llm_telemetry(_exc: Optional[BaseException]) -> None:
    try:
        from flask import g

        tok = getattr(g, "_llm_telemetry_token", None)
        if tok is not None:
            reset_llm_telemetry_context(tok)
    except Exception:
        pass
