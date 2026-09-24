"""LLM + deterministic gate: student-facing stems/options must match printed exam format.

Whack-a-mole regex fixes are not enough — extractors keep inventing new TeX variants.
This module:
1. Runs an LLM display review before questions are saved (preferred).
2. Always runs a deterministic finalize pass before student delivery (safety net for
   already-stored rows and LLM misses).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from app.services.quiz.math_text import (
    dedupe_repeated_math,
    merge_prose_and_math,
    normalize_latex_spacing,
    normalize_mixed_percents,
    normalize_plain_ellipsis,
    normalize_tex_set_braces,
    recover_fields,
    strip_english_dollar_spans,
    wrap_for_mathjax,
    _compact_math_key,
)
from app.services.quiz.retry_utils import invoke_structured

logger = logging.getLogger(__name__)


class DisplayReviewedOption(BaseModel):
    label: str = Field(..., description="A, B, C, or D")
    text: str = Field(..., description="Student-facing option text only")
    latex: Optional[str] = Field(
        None,
        description="TeX body only when needed for typesetting; never spacing-only junk",
    )


class DisplayReviewedQuestion(BaseModel):
    index: int = Field(..., description="0-based index matching the input batch")
    question_text: str = Field(..., description="Full stem exactly as a student should read it")
    question_latex: Optional[str] = Field(
        None,
        description="Extra math ONLY if not already inside question_text; else null",
    )
    options: List[DisplayReviewedOption] = Field(default_factory=list)


class DisplayReviewedBatch(BaseModel):
    questions: List[DisplayReviewedQuestion] = Field(default_factory=list)


_DISPLAY_REVIEW_PROMPT = """You are a QA editor for a school diagnostic exam UI.

Rewrite each MCQ so it matches how the printed paper looks for a student.
Do NOT invent new questions, change answers, shuffle options, or change meaning.

Hard rules for student-facing text:
1. question_text = the FULL stem once. Never append a second copy of equations, sequences,
   or expressions that already appear in the stem.
2. If the stem already contains the math/sequence, set question_latex to null.
3. Never leave raw TeX commands visible in text: no \\ldots, \\quad, \\,, \\tfrac, \\frac,
   \\%, \\mathrm, etc. Convert to plain student text when the paper uses plain text
   (e.g. "16 2/3%", "5.18181818 ...", "(6, 3)", "7, 12, 17, 22, ...").
4. Percent options: plain "15%", "16 2/3%", "33 1/3%" only.
5. Ordered pairs: "(6, 3)" with a normal space — never "(6,\\, 3)".
6. Keep option labels A–D and the same correct choice meaning.
7. Keep real math readable (exponents like x^2 or x^{{2}} are OK in latex field;
   do not dump \\frac into option text for simple percents).

Input JSON (array of {{index, question_text, question_latex, options}}):
{payload}

Return the reviewed questions with the same indexes.
"""


def strip_trailing_content_already_in_stem(text: str) -> str:
    """Drop a trailing chunk that is only a repeat of content already in the stem.

    Covers: duplicated equation systems AND duplicated sequences after '?'.
    """
    s = (text or "").strip()
    if not s:
        return s
    s = dedupe_repeated_math(s)
    m = re.search(r"^(.*\?)\s*(.+)$", s, re.S)
    if not m:
        return s
    prefix, rest = m.group(1).rstrip(), m.group(2).strip()
    if not rest:
        return prefix
    rest = dedupe_repeated_math(rest)
    rest_key = _compact_math_key(rest)
    if rest_key and rest_key in _compact_math_key(prefix):
        return prefix
    # Sequences / lists without "=" (e.g. "7, 12, 17, 22, ...")
    rest_norm = re.sub(r"\s+", "", rest.lower())
    prefix_norm = re.sub(r"\s+", "", prefix.lower())
    if len(rest_norm) >= 5 and rest_norm in prefix_norm:
        return prefix
    return f"{prefix} {rest}".strip()


def finalize_display_text(text: Optional[str], latex: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Deterministic student-facing cleanup (always safe to run at delivery)."""
    display, recovered = recover_fields(text, latex)
    body = merge_prose_and_math(display, recovered)
    body = strip_english_dollar_spans(body)
    body = normalize_mixed_percents(body)
    body = normalize_plain_ellipsis(body)
    body = normalize_latex_spacing(body)
    body = normalize_tex_set_braces(body)
    body = strip_trailing_content_already_in_stem(body)
    # If latex is fully inside the cleaned stem, drop it to avoid re-merge later.
    if recovered and _compact_math_key(recovered) in _compact_math_key(body):
        recovered = None
    return body, recovered


def finalize_option_text(text: Optional[str], latex: Optional[str] = None) -> tuple[str, Optional[str]]:
    display, recovered = recover_fields(text, latex)
    body = merge_prose_and_math(display, recovered)
    body = strip_english_dollar_spans(body)
    body = normalize_mixed_percents(body)
    body = normalize_plain_ellipsis(body)
    body = normalize_latex_spacing(body)
    body = normalize_tex_set_braces(body)
    if recovered and _compact_math_key(recovered) in _compact_math_key(body):
        # Prefer plain percent / pair text without a redundant latex twin.
        if re.match(r"^\d+(?:\s+\d+\s*/\s*\d+)?\s*%$", body.strip()) or re.match(
            r"^\(\s*-?\d+\s*,\s*-?\d+\s*\)$", body.strip()
        ):
            recovered = None
    return body, recovered


def student_render(text: Optional[str], latex: Optional[str] = None, *, inline: bool = True) -> str:
    """Final string MathJax should see — cleaned then delimited."""
    body, recovered = finalize_display_text(text, latex)
    return wrap_for_mathjax(merge_prose_and_math(body, recovered), inline=inline)


def _option_payload(opt: Any) -> dict:
    if isinstance(opt, dict):
        return {
            "label": str(opt.get("label") or "").strip().upper()[:1],
            "text": opt.get("text") or "",
            "latex": opt.get("latex"),
        }
    return {
        "label": str(getattr(opt, "label", "") or "").strip().upper()[:1],
        "text": getattr(opt, "text", "") or "",
        "latex": getattr(opt, "latex", None),
    }


def review_mcqs_for_display(mcqs: List[Any]) -> List[Any]:
    """LLM review pass before save. On failure, returns inputs unchanged (finalize still runs later)."""
    if not mcqs:
        return mcqs

    from app.utils.llm_factory import get_chat_model

    payload = []
    for i, mcq in enumerate(mcqs):
        if hasattr(mcq, "question_text"):
            opts = [_option_payload(o) for o in (mcq.options or [])]
            payload.append(
                {
                    "index": i,
                    "question_text": mcq.question_text,
                    "question_latex": getattr(mcq, "question_latex", None),
                    "options": opts,
                }
            )
        elif isinstance(mcq, dict):
            payload.append(
                {
                    "index": i,
                    "question_text": mcq.get("question_text") or "",
                    "question_latex": mcq.get("question_latex"),
                    "options": [_option_payload(o) for o in (mcq.get("options") or [])],
                }
            )

    llm = get_chat_model(temperature=0.0, max_tokens=8192)
    batch_size = 5
    by_index: dict[int, DisplayReviewedQuestion] = {}
    for start in range(0, len(payload), batch_size):
        chunk = payload[start : start + batch_size]
        try:
            result: DisplayReviewedBatch = invoke_structured(
                llm,
                DisplayReviewedBatch,
                _DISPLAY_REVIEW_PROMPT.format(payload=json.dumps(chunk, ensure_ascii=False)),
            )
        except Exception as exc:
            logger.warning("Display format LLM review failed for batch %s: %s", start, exc)
            continue
        for item in result.questions or []:
            by_index[int(item.index)] = item

    if not by_index:
        return mcqs

    out = []
    for i, mcq in enumerate(mcqs):
        reviewed = by_index.get(i)
        if not reviewed:
            out.append(mcq)
            continue
        q_text, q_latex = finalize_display_text(reviewed.question_text, reviewed.question_latex)
        if hasattr(mcq, "model_copy"):
            label_map = {
                str(o.label).strip().upper()[:1]: o for o in (reviewed.options or [])
            }
            new_opts = []
            for opt in mcq.options:
                lab = str(opt.label).strip().upper()[:1]
                src = label_map.get(lab)
                if src:
                    ot, ol = finalize_option_text(src.text, src.latex)
                    new_opts.append(opt.model_copy(update={"text": ot, "latex": ol}))
                else:
                    ot, ol = finalize_option_text(opt.text, opt.latex)
                    new_opts.append(opt.model_copy(update={"text": ot, "latex": ol}))
            out.append(
                mcq.model_copy(
                    update={
                        "question_text": q_text,
                        "question_latex": q_latex,
                        "options": new_opts or mcq.options,
                    }
                )
            )
        elif isinstance(mcq, dict):
            cleaned_opts = []
            label_map = {
                str(o.label).strip().upper()[:1]: o for o in (reviewed.options or [])
            }
            for opt in mcq.get("options") or []:
                lab = str(opt.get("label") or "").strip().upper()[:1]
                src = label_map.get(lab)
                if src:
                    ot, ol = finalize_option_text(src.text, src.latex)
                    cleaned_opts.append({**opt, "text": ot, "latex": ol})
                else:
                    ot, ol = finalize_option_text(opt.get("text"), opt.get("latex"))
                    cleaned_opts.append({**opt, "text": ot, "latex": ol})
            out.append(
                {
                    **mcq,
                    "question_text": q_text,
                    "question_latex": q_latex,
                    "options": cleaned_opts or mcq.get("options"),
                }
            )
        else:
            out.append(mcq)
    return out
