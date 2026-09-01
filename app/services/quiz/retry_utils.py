"""Retry helpers for structured LLM output validation."""
from __future__ import annotations

import ast
import json
import logging
import re
from typing import Callable, List, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def parse_groq_failed_generation(exc: BaseException, model: Type[T]) -> T | None:
    """Recover valid structured output when Groq returns tool_use_failed with failed_generation."""
    candidates: list[object] = []
    body = getattr(exc, "body", None)
    if body is not None:
        candidates.append(body)
    candidates.append(str(exc))

    for payload in candidates:
        failed_raw = None
        if isinstance(payload, dict):
            err = payload.get("error", payload)
            if isinstance(err, dict):
                failed_raw = err.get("failed_generation")
        elif isinstance(payload, str) and "failed_generation" in payload:
            start = payload.find("{")
            if start >= 0:
                snippet = payload[start:]
                try:
                    outer = ast.literal_eval(snippet)
                    if isinstance(outer, dict):
                        err = outer.get("error", outer)
                        if isinstance(err, dict):
                            failed_raw = err.get("failed_generation")
                except (SyntaxError, ValueError):
                    match = re.search(
                        r"failed_generation['\"]:\s*('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")",
                        payload,
                        re.DOTALL,
                    )
                    if match:
                        try:
                            failed_raw = ast.literal_eval(match.group(1))
                        except (SyntaxError, ValueError):
                            failed_raw = None
        if not failed_raw:
            continue
        try:
            data = json.loads(failed_raw) if isinstance(failed_raw, str) else failed_raw
            return model.model_validate(data)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as parse_exc:
            logger.debug("Could not parse Groq failed_generation: %s", parse_exc)
    return None


def invoke_structured(llm, model: Type[T], prompt: str) -> T:
    """Invoke LLM structured output with Groq tool-failure recovery."""
    methods: list[str | None] = ["json_mode", None]
    last_exc: Exception | None = None
    for method in methods:
        try:
            if method:
                chain = llm.with_structured_output(model, method=method)
            else:
                chain = llm.with_structured_output(model)
            return chain.invoke(prompt)
        except Exception as exc:
            recovered = parse_groq_failed_generation(exc, model)
            if recovered is not None:
                logger.warning(
                    "Recovered %s from Groq failed_generation after structured output error",
                    model.__name__,
                )
                return recovered
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def retry_on_validation_error(
    fn: Callable[[], T],
    *,
    max_retries: int = 2,
    on_retry: Callable[[ValidationError, int], None] | None = None,
) -> T:
    """Call fn; on Pydantic ValidationError retry up to max_retries times."""
    last_error: ValidationError | ValueError | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except (ValidationError, ValueError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            logger.warning("Validation failed (attempt %s/%s): %s", attempt + 1, max_retries + 1, exc)
            if on_retry:
                on_retry(exc, attempt + 1)
    assert last_error is not None
    raise last_error


def format_validation_errors(exc: ValidationError) -> str:
    """Human-readable validation error summary for LLM retry prompts."""
    parts: List[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)
