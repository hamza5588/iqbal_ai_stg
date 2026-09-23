"""Retry helpers for structured LLM output validation."""
from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any, Callable, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_FAILED_GEN_KEY_RE = re.compile(
    r"['\"]failed_generation['\"]\s*:\s*",
    re.IGNORECASE,
)


def _unescape_json_string_fragment(raw: str) -> str:
    """Best-effort unescape of a JSON/Python string body (may be truncated)."""
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(f'"{raw}"')
    except (SyntaxError, ValueError):
        pass
    return (
        raw.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\'", "'")
        .replace("\\\\", "\\")
    )


def _extract_failed_generation_raw(payload: object) -> Optional[str]:
    """Pull failed_generation text from Groq/OpenAI error bodies (incl. truncated)."""
    if isinstance(payload, dict):
        err = payload.get("error", payload)
        if isinstance(err, dict):
            failed = err.get("failed_generation")
            if isinstance(failed, str) and failed.strip():
                return failed
            if isinstance(failed, dict):
                try:
                    return json.dumps(failed)
                except (TypeError, ValueError):
                    return str(failed)
        return None

    if not isinstance(payload, str) or "failed_generation" not in payload:
        return None

    # Prefer full dict parse when the error string is complete.
    start = payload.find("{")
    if start >= 0:
        snippet = payload[start:]
        try:
            outer = ast.literal_eval(snippet)
            if isinstance(outer, dict):
                err = outer.get("error", outer)
                if isinstance(err, dict):
                    failed = err.get("failed_generation")
                    if isinstance(failed, str) and failed.strip():
                        return failed
        except (SyntaxError, ValueError):
            pass

    match = _FAILED_GEN_KEY_RE.search(payload)
    if not match:
        return None
    rest = payload[match.end() :]
    if not rest:
        return None

    opener = rest[0]
    if opener in "'\"":
        body = rest[1:]
        # Truncated generations often lack a closing quote — take the remainder.
        end = None
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                i += 2
                continue
            if ch == opener:
                end = i
                break
            i += 1
        fragment = body if end is None else body[:end]
        return _unescape_json_string_fragment(fragment)

    # Bare JSON object after the key.
    brace = rest.find("{")
    if brace >= 0:
        return rest[brace:].rstrip().rstrip("}'\"")
    return rest.strip() or None


def _iter_balanced_objects(text: str) -> List[str]:
    """Return complete top-level `{...}` slices inside text (string-aware)."""
    objects: List[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                objects.append(text[start : i + 1])
                start = -1
    return objects


def _close_truncated_json(text: str) -> Optional[str]:
    """Close open braces/brackets after stripping an incomplete trailing token."""
    s = text.rstrip()
    if not s:
        return None

    # Drop a trailing incomplete string value when quotes are unbalanced.
    in_string = False
    escape = False
    last_string_start = -1
    for i, ch in enumerate(s):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            last_string_start = i
    if in_string and last_string_start >= 0:
        s = s[:last_string_start].rstrip().rstrip(",")

    # Trim dangling commas / colons before closing.
    s = s.rstrip()
    while s and s[-1] in ",:":
        s = s[:-1].rstrip()

    stack: List[str] = []
    in_string = False
    escape = False
    for ch in s:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()

    if in_string:
        s += '"'
    s += "".join(reversed(stack))
    return s


def _salvage_partial_structured_json(text: str) -> Optional[dict]:
    """Rebuild a usable object from truncated tool JSON (keep complete questions)."""
    brace = text.find("{")
    if brace < 0:
        return None
    fragment = text[brace:]

    title = None
    title_m = re.search(r'"title"\s*:\s*"((?:\\.|[^"\\])*)"', fragment)
    if title_m:
        title = _unescape_json_string_fragment(title_m.group(1))

    format_detected = "unknown"
    fmt_m = re.search(r'"format_detected"\s*:\s*"((?:\\.|[^"\\])*)"', fragment)
    if fmt_m:
        format_detected = _unescape_json_string_fragment(fmt_m.group(1))

    confidence = 0.7
    conf_m = re.search(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)', fragment)
    if conf_m:
        try:
            confidence = float(conf_m.group(1))
        except ValueError:
            confidence = 0.7

    questions: List[Any] = []
    q_idx = fragment.find('"questions"')
    if q_idx >= 0:
        arr_start = fragment.find("[", q_idx)
        if arr_start >= 0:
            for obj_text in _iter_balanced_objects(fragment[arr_start:]):
                try:
                    questions.append(json.loads(obj_text))
                except json.JSONDecodeError:
                    continue

    answers: List[Any] = []
    a_idx = fragment.find('"answers"')
    if a_idx >= 0:
        arr_start = fragment.find("[", a_idx)
        if arr_start >= 0:
            for obj_text in _iter_balanced_objects(fragment[arr_start:]):
                try:
                    answers.append(json.loads(obj_text))
                except json.JSONDecodeError:
                    continue

    if not questions and not answers:
        return None

    return {
        "title": title,
        "questions": questions,
        "answers": answers,
        "format_detected": format_detected or "unknown",
        "confidence": max(0.0, min(1.0, confidence)),
        "warnings": ["Recovered partial structured output from truncated model response"],
    }


def _coerce_failed_generation_data(failed_raw: object) -> Optional[object]:
    if isinstance(failed_raw, dict):
        return failed_raw
    if not isinstance(failed_raw, str):
        return None

    text = failed_raw.strip()
    if not text:
        return None

    # Tool-call wrapper: PDFExtractionResult({...}) or name + JSON body.
    if not text.startswith("{") and not text.startswith("["):
        brace = text.find("{")
        if brace >= 0:
            text = text[brace:]

    candidates: List[str] = [text]
    closed = _close_truncated_json(text)
    if closed and closed not in candidates:
        candidates.append(closed)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    salvaged = _salvage_partial_structured_json(text)
    if salvaged is not None:
        return salvaged
    return None


def parse_groq_failed_generation(exc: BaseException, model: Type[T]) -> T | None:
    """Recover valid structured output when Groq returns tool_use_failed with failed_generation."""
    payloads: list[object] = []
    body = getattr(exc, "body", None)
    if body is not None:
        payloads.append(body)
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payloads.append(response.json())
        except Exception:
            pass
    payloads.append(str(exc))

    for payload in payloads:
        failed_raw = _extract_failed_generation_raw(payload)
        if not failed_raw:
            continue
        data = _coerce_failed_generation_data(failed_raw)
        if data is None:
            logger.debug("Could not coerce Groq failed_generation into JSON")
            continue
        try:
            return model.model_validate(data)
        except (ValidationError, TypeError, ValueError) as parse_exc:
            logger.debug("Could not validate Groq failed_generation: %s", parse_exc)
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
