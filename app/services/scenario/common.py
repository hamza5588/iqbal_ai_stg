"""Shared helpers: prompt files, JSON columns, structured LLM calls, text utils."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Iterable, List, Optional, Type, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")
# Bump when any prompt file changes meaning: stored on every evaluation.
PROMPT_VERSION = "sba-v1"

DEFAULT_RUBRIC = [
    {"key": "conceptual_accuracy", "label": "Conceptual accuracy", "weight": 40},
    {"key": "scenario_application", "label": "Scenario application", "weight": 30},
    {"key": "logical_reasoning", "label": "Logical reasoning", "weight": 20},
    {"key": "clarity_completeness", "label": "Clarity and completeness", "weight": 10},
]

T = TypeVar("T", bound=BaseModel)


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    with open(os.path.join(PROMPT_DIR, f"{name}.md"), encoding="utf-8") as fh:
        return fh.read()


def render_prompt(name: str, **values: Any) -> str:
    # str.format would choke on braces inside documents/answers: substitute only our keys.
    text = load_prompt(name)
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value if value is not None else ""))
    return text


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def text_key(text: str) -> str:
    return hashlib.sha256(norm_text(re.sub(r"[^\w\s]", " ", text or "")).encode("utf-8")).hexdigest()


def tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2]


def jaccard(a: str, b: str) -> float:
    sa, sb = set(tokens(a)), set(tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def clean_list(values: Iterable[Any], *, limit: int = 12, max_len: int = 400) -> List[str]:
    out: List[str] = []
    for v in values or []:
        s = re.sub(r"\s+", " ", str(v or "")).strip()
        if s and s not in out:
            out.append(s[:max_len])
        if len(out) >= limit:
            break
    return out


def wrap_untrusted(text: str) -> str:
    """Neutralise tag look-alikes so untrusted text cannot close our delimiters."""
    return re.sub(r"</?\s*(student_answer|learning_content|reference_answers|evaluation_criteria|question|"
                  r"sample_questions|teacher_instructions|document|system)\b[^>]*>",
                  "[tag removed]", text or "", flags=re.IGNORECASE)


def _model_name(llm: Any) -> str:
    # get_chat_model returns a telemetry wrapper; the real model sits on it or on .bound.
    for obj in (llm, getattr(llm, "bound", None)):
        for attr in ("telemetry_model", "model_name", "model"):
            value = getattr(obj, attr, None) if obj is not None else None
            if value and value != "unknown":
                return str(value)
    return "unknown"


def _parse_json_content(raw: Any, schema: Type[T]) -> T:
    text = raw if isinstance(raw, str) else "".join(
        part.get("text", "") if isinstance(part, dict) else str(part) for part in (raw or [])
    )
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model returned no JSON object")
    return schema.model_validate_json(text[start:end + 1])


def _recover_failed_generation(exc: Exception, schema: Type[T]) -> Optional[T]:
    """Groq 'tool_use_failed': the model called a misnamed tool but its arguments are
    usually a complete, valid payload. Validate them against the schema instead of
    discarding a correct answer. Anything that does not validate is still rejected."""
    msg = str(exc)
    if "failed_generation" not in msg:
        return None
    body = getattr(exc, "body", None)
    raw = None
    if isinstance(body, dict):
        raw = (body.get("error") or body).get("failed_generation")
    if raw is None:
        m = re.search(r"'failed_generation': '(.*)'\}\}\s*$", msg, flags=re.S)
        raw = m.group(1).encode("utf-8").decode("unicode_escape", errors="ignore") if m else None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("arguments"), dict):
        data = data["arguments"]
    try:
        return schema.model_validate(data)
    except Exception:  # noqa: BLE001
        return None


def structured_call(schema: Type[T], prompt: str, *, description: str, max_tokens: int = 4096) -> tuple[T, str]:
    """One deterministic structured LLM call through the shared Groq limiter.

    Uses provider-native JSON-schema mode first (same approach as the hybrid quiz
    pipeline; avoids Groq tool-name validation failures), then tool-calling structured
    output as a fallback. Output is always validated against ``schema``.
    Returns (parsed_model, model_name). Raises on provider failure or invalid output.
    """
    from app.utils.groq_rate_limit import invoke_with_groq_rate_limit
    from app.utils.llm_factory import get_chat_model

    llm = get_chat_model(temperature=0, max_tokens=max_tokens)
    model = _model_name(llm)
    try:
        bound = llm.bind(response_format={
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "strict": False, "schema": schema.model_json_schema()},
        })
        msg = invoke_with_groq_rate_limit(lambda: bound.invoke(prompt), description=description)
        return _parse_json_content(getattr(msg, "content", msg), schema), model
    except Exception as exc:  # noqa: BLE001 - fall back to tool-calling structured output
        recovered = _recover_failed_generation(exc, schema)
        if recovered is not None:
            return recovered, model
        logger.info("%s: json_schema mode failed (%s); falling back to tool calling", description, str(exc)[:200])

    structured = llm.with_structured_output(schema)
    try:
        result = invoke_with_groq_rate_limit(lambda: structured.invoke(prompt), description=description)
    except Exception as exc:  # noqa: BLE001
        recovered = _recover_failed_generation(exc, schema)
        if recovered is None:
            raise
        result = recovered
    if result is None:
        raise ValueError(f"{description}: model returned no structured output")
    if isinstance(result, dict):
        result = schema.model_validate(result)
    if not isinstance(result, schema):
        raise ValueError(f"{description}: unexpected output type {type(result).__name__}")
    return result, model
