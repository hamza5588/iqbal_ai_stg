"""Second-opinion check for AI-generated MCQ answer keys (Learning Chat queues).

Generated practice questions occasionally carry a wrong key, or no correct
option at all (seen in QA: "mean of 4, 8, 6, 5, 3" with options 5/6/4/7 and
key "6"). One batched LLM call solves every queued question independently;
items whose stored key disagrees are dropped before a student sees them.

Fail-safe: any error, or a check that would drop every item, returns the
queue unchanged so Learning Chat never breaks because of the checker.
"""
from __future__ import annotations

import logging
from typing import List

from pydantic import BaseModel, Field

from app.utils.groq_rate_limit import invoke_with_groq_rate_limit
from app.utils.llm_factory import get_chat_model

logger = logging.getLogger(__name__)

_PROMPT = """You are checking multiple-choice maths questions for school students.
Solve EACH question yourself, carefully and step by step (work silently), then report
the label of the option that is correct. If no option is correct, or more than one is,
answer "NONE". Do not trust any hint about which answer is expected.

{block}
"""


class _Verdict(BaseModel):
    index: int = Field(..., description="The question number given in the list")
    answer_label: str = Field(..., description='Correct option label (e.g. "B"), or "NONE"')


class _Verdicts(BaseModel):
    items: List[_Verdict] = Field(default_factory=list)


def _block(items: List[dict]) -> str:
    lines = []
    for i, q in enumerate(items):
        text = (q.get("question_text") or q.get("question_latex") or "").strip()
        lines.append(f"Q{i}: {text}")
        for o in q.get("options") or []:
            lines.append(f"   {o.get('label')}) {o.get('text') or o.get('latex') or ''}")
    return "\n".join(lines)


def verify_mcq_keys(items: List[dict]) -> List[dict]:
    """Keep only items whose correct_option_index matches an independent solve."""
    if not items:
        return items
    try:
        llm = get_chat_model(temperature=0.0, max_tokens=1024)
        structured = llm.with_structured_output(_Verdicts)
        result: _Verdicts = invoke_with_groq_rate_limit(
            lambda: structured.invoke(_PROMPT.format(block=_block(items))),
            description="learning chat answer-key check",
        )
    except Exception as exc:  # noqa: BLE001 - never block question generation
        logger.warning("MCQ answer-key check failed; keeping queue unchanged: %s", exc)
        return items

    verdicts = {v.index: (v.answer_label or "").strip().upper() for v in (result.items or [])}
    kept, dropped = [], []
    for i, q in enumerate(items):
        opts = q.get("options") or []
        idx = q.get("correct_option_index")
        stored = (opts[idx].get("label") or "").strip().upper() if isinstance(idx, int) and 0 <= idx < len(opts) else ""
        verdict = verdicts.get(i)
        if verdict is None or verdict == stored:
            kept.append(q)  # unchecked items are kept (no evidence against them)
        else:
            dropped.append((q.get("question_text") or "")[:80] + f" [key {stored}, check {verdict}]")
    if dropped:
        logger.warning("Dropped %d MCQ(s) with a disputed answer key: %s", len(dropped), dropped)
    if not kept:
        logger.warning("Answer-key check disputed every question; keeping queue unchanged")
        return items
    return kept
