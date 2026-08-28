"""Retry helpers for structured LLM output validation."""
from __future__ import annotations

import logging
from typing import Callable, List, TypeVar

from pydantic import ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_on_validation_error(
    fn: Callable[[], T],
    *,
    max_retries: int = 2,
    on_retry: Callable[[ValidationError, int], None] | None = None,
) -> T:
    """Call fn; on Pydantic ValidationError retry up to max_retries times."""
    last_error: ValidationError | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except ValidationError as exc:
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
