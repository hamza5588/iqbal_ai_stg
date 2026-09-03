"""
Shared Groq rate-limit utilities.

Provides:
- parse_groq_error()  — normalise any Groq exception to GroqRateLimitInfo
- compute_retry_delay() — header-first, fallback exponential + jitter
- GroqRateLimitError   — raised when retries are exhausted for a rate-limit condition
- GroqBusyError        — raised when the semaphore can't be acquired in time

Both the student path (lesson_qa_graph / student_service) and the teacher path
(rag_service / rag_routes) import from here so error handling is consistent.
"""
from __future__ import annotations

import logging
import os
import random
import re
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

class GroqRateLimitInfo:
    """Normalised rate-limit metadata extracted from a Groq exception."""
    __slots__ = ("kind", "retry_after", "is_retryable", "is_daily_limit", "raw_message")

    def __init__(
        self,
        kind: str,
        retry_after: int,
        is_retryable: bool,
        is_daily_limit: bool,
        raw_message: str = "",
    ) -> None:
        self.kind = kind
        self.retry_after = retry_after
        self.is_retryable = is_retryable
        self.is_daily_limit = is_daily_limit
        self.raw_message = raw_message

    def __repr__(self) -> str:
        return (
            f"GroqRateLimitInfo(kind={self.kind!r}, retry_after={self.retry_after}, "
            f"is_retryable={self.is_retryable}, is_daily_limit={self.is_daily_limit})"
        )


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class GroqRateLimitError(Exception):
    """Raised when Groq rate limits are hit and all retries are exhausted."""
    def __init__(self, info: GroqRateLimitInfo) -> None:
        self.info = info
        super().__init__(f"Groq rate limit exhausted: {info.kind} (retry_after={info.retry_after}s)")


class GroqBusyError(Exception):
    """Raised when the shared Groq semaphore cannot be acquired within the timeout."""
    pass


# ---------------------------------------------------------------------------
# Header parsing helpers
# ---------------------------------------------------------------------------

def _parse_seconds_from_header(value: str) -> Optional[int]:
    """
    Parse a Groq reset/retry header to integer seconds.

    Handles formats:
    - Pure integer or float  ("30", "30.5")
    - Duration string        ("1m30s", "60s", "2h", "1m")
    - ISO 8601 partial       ("PT1M30S") — best-effort
    """
    if not value:
        return None
    value = value.strip()

    # Pure number (seconds or sub-seconds)
    try:
        return max(1, int(float(value)))
    except ValueError:
        pass

    # ISO 8601 duration like PT1M30S
    iso = re.match(
        r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$",
        value.upper(),
    )
    if iso:
        days, hours, mins, secs = (float(g or 0) for g in iso.groups())
        total = int(days * 86400 + hours * 3600 + mins * 60 + secs)
        return max(1, total) if total else None

    # Natural duration: "1m30s", "90s", "2h"
    total = 0
    matched = False
    for unit, factor in [("h", 3600), ("m", 60), ("s", 1)]:
        m = re.search(rf"(\d+(?:\.\d+)?)\s*{unit}", value, re.IGNORECASE)
        if m:
            total += int(float(m.group(1))) * factor
            matched = True
    if matched:
        return max(1, total)

    return None


def _extract_headers(exc: Exception) -> dict:
    """Best-effort extraction of HTTP headers from a Groq SDK exception."""
    # groq.APIStatusError attaches .response (httpx.Response) which has .headers
    if hasattr(exc, "response") and hasattr(exc.response, "headers"):
        try:
            return {k.lower(): v for k, v in exc.response.headers.items()}
        except Exception:
            pass
    # Some SDK versions attach .headers directly
    if hasattr(exc, "headers") and exc.headers:
        try:
            return {k.lower(): v for k, v in exc.headers.items()}
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Core parsing
# ---------------------------------------------------------------------------

def parse_groq_error(exc: Exception) -> GroqRateLimitInfo:
    """
    Classify a Groq (or Groq-via-LangChain) exception into a GroqRateLimitInfo.

    Priority for retry_after:
    1. retry-after header
    2. x-ratelimit-reset-requests header
    3. x-ratelimit-reset-tokens header
    4. Sensible default based on error type

    Returns a GroqRateLimitInfo describing what happened and how to recover.
    """
    raw_message = str(exc)
    error_text = raw_message.lower()
    headers = _extract_headers(exc)

    # ---- retry_after from headers (priority order) -------------------------
    retry_after: Optional[int] = None
    for header in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        val = headers.get(header)
        if val:
            parsed = _parse_seconds_from_header(str(val))
            if parsed is not None:
                retry_after = parsed
                logger.debug("parse_groq_error: retry_after=%ds from header '%s'", retry_after, header)
                break

    # ---- classify error type -----------------------------------------------
    remaining_requests = headers.get("x-ratelimit-remaining-requests", "").strip()
    daily_exhausted_by_header = (
        remaining_requests == "0"
        or remaining_requests == "0.0"
    )
    daily_exhausted_by_text = any(
        kw in error_text
        for kw in ("per day", "daily", "rpd", "requests per day", "rate_limit_exceeded_daily")
    )
    is_daily_limit = daily_exhausted_by_header or daily_exhausted_by_text

    is_rate_limit = (
        "429" in error_text
        or "rate_limit" in error_text
        or "rate limit" in error_text
        or "tokens per minute" in error_text
        or "requests per minute" in error_text
        or "tpm" in error_text
        or "rpm" in error_text
        or "rateLimitError" in raw_message  # groq SDK exception class name
    )

    is_transient = (
        "500" in error_text
        or "502" in error_text
        or "503" in error_text
        or "504" in error_text
        or "connection" in error_text
        or "timeout" in error_text
        or "network" in error_text
        or "read timeout" in error_text
    )

    if is_rate_limit:
        kind = "RPD_EXHAUSTED" if is_daily_limit else "TPM_RATE_LIMITED"
        if retry_after is None:
            retry_after = 3600 if is_daily_limit else int(os.getenv("GROQ_DEFAULT_RETRY_AFTER_SECONDS", "15"))
        return GroqRateLimitInfo(
            kind=kind,
            retry_after=retry_after,
            # Daily limits are not retryable in the short term
            is_retryable=not is_daily_limit,
            is_daily_limit=is_daily_limit,
            raw_message=raw_message,
        )

    if is_transient:
        return GroqRateLimitInfo(
            kind="TRANSIENT_SERVER_ERROR",
            retry_after=retry_after or 5,
            is_retryable=True,
            is_daily_limit=False,
            raw_message=raw_message,
        )

    return GroqRateLimitInfo(
        kind="PROVIDER_ERROR",
        retry_after=0,
        is_retryable=False,
        is_daily_limit=False,
        raw_message=raw_message,
    )


# ---------------------------------------------------------------------------
# Delay computation
# ---------------------------------------------------------------------------

def compute_retry_delay(
    info: GroqRateLimitInfo,
    attempt: int,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
) -> float:
    """
    Compute how many seconds to wait before retrying.

    Header value wins when available (+ small jitter to spread thundering herd).
    Falls back to exponential backoff with full jitter.
    """
    if info.retry_after and info.retry_after > 0:
        # Cap at max_delay so we don't wait unreasonably long inside a single request
        base = min(info.retry_after, max_delay)
        jitter = random.uniform(0.0, min(2.0, base * 0.1))
        return base + jitter

    # Exponential backoff + full jitter (no thundering herd)
    exp = min(base_delay * (2 ** attempt), max_delay)
    jitter = random.uniform(0.0, exp)
    return jitter  # full-jitter: delay in [0, exp)


# ---------------------------------------------------------------------------
# Generic call wrapper (bounded concurrency + header-aware retry)
# ---------------------------------------------------------------------------

def invoke_with_groq_rate_limit(
    fn: Callable[[], Any],
    *,
    description: str = "groq call",
    max_retries: Optional[int] = None,
) -> Any:
    """
    Run ``fn()`` (one Groq request) through the shared process-wide Groq
    concurrency limiter with header-aware retry.

    This is the provider-agnostic sibling of the ``_invoke_with_groq_rate_limit``
    helpers in the lesson services — use it anywhere a raw ``structured.invoke`` /
    ``llm.invoke`` would otherwise hit Groq with no back-pressure (LMS diagnostic
    weakness analysis, Learning Chat MCQ generation, deficiency tutor).

    Behaviour:
    - Acquires a concurrency slot first (may raise ``GroqBusyError`` on timeout).
    - Retries ``TPM_RATE_LIMITED`` / ``TRANSIENT_SERVER_ERROR`` up to
      ``GROQ_MAX_RETRIES`` (default 3) using the provider's ``retry-after`` hint.
    - Raises ``GroqRateLimitError`` once retries are exhausted for a rate-limit
      condition, or immediately for a daily (RPD) cap.
    - Re-raises any other exception unchanged.
    """
    # Lazy import: rag_service imports this module, so importing it at module
    # load time would be circular.
    from app.utils.rag_service import groq_rate_limiter

    if max_retries is None:
        max_retries = max(0, int(os.getenv("GROQ_MAX_RETRIES", "3")))

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        groq_rate_limiter.wait_if_needed()  # may raise GroqBusyError
        try:
            result = fn()
            groq_rate_limiter.record_success()
            return result
        except (GroqRateLimitError, GroqBusyError):
            groq_rate_limiter.release_slot()
            raise
        except Exception as exc:  # noqa: BLE001 — classified below
            groq_rate_limiter.release_slot()
            info = parse_groq_error(exc)
            last_exc = exc

            if info.kind in ("TPM_RATE_LIMITED", "TRANSIENT_SERVER_ERROR") and info.is_retryable:
                groq_rate_limiter.record_429_error()
                if attempt < max_retries:
                    delay = compute_retry_delay(info, attempt)
                    logger.warning(
                        "invoke_with_groq_rate_limit: %s on '%s' (attempt %d/%d), retrying in %.1fs",
                        info.kind, description, attempt + 1, max_retries, delay,
                    )
                    time.sleep(delay)
                    continue
                raise GroqRateLimitError(info) from exc

            if info.kind == "RPD_EXHAUSTED":
                groq_rate_limiter.record_429_error()
                raise GroqRateLimitError(info) from exc

            # Non-retryable provider error — surface the original.
            raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("invoke_with_groq_rate_limit: unexpected exit from retry loop")
