"""Answer-key gate for AI-generated MCQs before they reach a student.

Generated practice questions often carry a wrong key, or no correct option at
all: in Oct 2026, 50 of 134 AI-generated Learning Chat questions did ("3(2x-5) =
4x+7" keyed "x = 4"; the true answer 11 was not an option). Every question has
to pass, in order:

1. ``mcq_math_verify.structural_problems`` - 4 distinct, non-empty options, a key
   that points to one of them, a readable stem.
2. ``mcq_math_verify.check_answer_key`` - sympy works the answer out for the
   shapes it can read; a disproved key (wrong / no correct option / two correct
   options) is dropped, a confirmed one is kept without an LLM call.
3. An independent LLM solve for everything sympy could not read. The model
   answers in plain text ("Q0: B"): the old tool-call version failed 74 times
   on Groq with ``tool_use_failed`` because the model replied in text.

Fail-closed: a question nobody could confirm is dropped. Callers top the queue
up with new questions; a short queue is better than a wrong answer key.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Sequence

from app.services.quiz.mcq_math_verify import OK, check_answer_key, structural_problems
from app.utils.groq_rate_limit import invoke_with_groq_rate_limit
from app.utils.llm_factory import get_chat_model

logger = logging.getLogger(__name__)

_LLM_BATCH = 5

_PROMPT = """You are checking multiple-choice maths questions for school students.
Solve EACH question yourself, carefully, then report the label of the correct option.
Answer "NONE" if no option is correct or if more than one option is correct.
Do not trust any hint about which answer is expected.

Reply with exactly one line per question and nothing else, in this form:
Q0: B
Q1: NONE

{block}
"""

_VERDICT_LINE_RE = re.compile(r"^[\s*#>-]*Q\s*(\d+)\s*[:.)\-–]\s*\**\s*\(?\s*(NONE|[A-D])\b", re.I | re.M)


def _block(items: Sequence[dict]) -> str:
    lines = []
    for i, q in enumerate(items):
        text = (q.get("question_text") or q.get("question_latex") or "").strip()
        latex = (q.get("question_latex") or "").strip()
        if latex and latex not in text:
            text = f"{text} {latex}"
        lines.append(f"Q{i}: {text}")
        for o in q.get("options") or []:
            lines.append(f"   {o.get('label')}) {o.get('text') or o.get('latex') or ''}")
    return "\n".join(lines)


def parse_verdicts(text: str) -> Dict[int, str]:
    """``Q<n>: <label>`` lines -> {n: "A".."D" | "NONE"}; the first verdict per question wins."""
    verdicts: Dict[int, str] = {}
    for m in _VERDICT_LINE_RE.finditer(text or ""):
        verdicts.setdefault(int(m.group(1)), m.group(2).upper())
    return verdicts


def _response_text(resp) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, list):  # some providers return content blocks
        content = " ".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
    return content if isinstance(content, str) else str(content or "")


def _llm_verdicts(items: Sequence[dict]) -> Optional[Dict[int, str]]:
    """Independent solve of ``items``; None when the model could not be asked."""
    try:
        base_llm = get_chat_model(temperature=0.0, max_tokens=8192)
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCQ answer-key check: no LLM available: %s", exc)
        return None
    model_name = str(getattr(base_llm, "model_name", "") or getattr(base_llm, "model", "")).lower()
    # GPT-OSS takes low/medium/high; solving maths keys is worth "medium". Other models
    # run with their own defaults (see practice_service._generate_hint).
    attempts = [{"reasoning_effort": "medium"}, {}] if "gpt-oss" in model_name else [{}]
    prompt = _PROMPT.format(block=_block(items))
    for bind_kwargs in attempts:
        try:
            llm = base_llm.bind(**bind_kwargs) if bind_kwargs else base_llm
            resp = invoke_with_groq_rate_limit(
                lambda: llm.invoke(prompt), description="learning chat answer-key check"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCQ answer-key check LLM call failed: %s", exc)
            continue
        verdicts = parse_verdicts(_response_text(resp))
        if verdicts:
            return verdicts
        logger.warning("MCQ answer-key check: no verdict lines in reply: %r", _response_text(resp)[:200])
    return None


def _stored_label(q: dict) -> str:
    opts = q.get("options") or []
    idx = q.get("correct_option_index")
    if isinstance(idx, int) and 0 <= idx < len(opts):
        return (opts[idx].get("label") or "ABCD"[idx]).strip().upper()
    return ""


def verify_mcq_keys(items: List[dict]) -> List[dict]:
    """Keep only items whose answer key was confirmed (sympy or an independent LLM solve)."""
    if not items:
        return items
    kept: Dict[int, dict] = {}
    pending: List[int] = []
    dropped: List[str] = []
    for i, q in enumerate(items):
        problems = structural_problems(q)
        if problems:
            dropped.append(f"{(q.get('question_text') or '')[:70]} [{'; '.join(problems)}]")
            continue
        verdict = check_answer_key(q)
        if verdict.disproved:
            dropped.append(f"{(q.get('question_text') or '')[:70]} [{verdict.status}]")
        elif verdict.status == OK:
            kept[i] = {**q, "key_verified": "math"}
        else:
            pending.append(i)

    for start in range(0, len(pending), _LLM_BATCH):
        batch_ids = pending[start : start + _LLM_BATCH]
        batch = [items[i] for i in batch_ids]
        verdicts = _llm_verdicts(batch)
        for pos, i in enumerate(batch_ids):
            q = items[i]
            stored = _stored_label(q)
            verdict = (verdicts or {}).get(pos)
            if verdict and stored and verdict == stored:
                kept[i] = {**q, "key_verified": "llm"}
            else:
                why = "checker unavailable" if verdicts is None else f"key {stored}, check {verdict or 'missing'}"
                dropped.append(f"{(q.get('question_text') or '')[:70]} [{why}]")

    if dropped:
        logger.warning("Dropped %d of %d MCQ(s) with an unconfirmed answer key: %s", len(dropped), len(items), dropped)
    return [kept[i] for i in sorted(kept)]


def verify_mcq_models(mcqs: list) -> list:
    """``verify_mcq_keys`` for ``MCQQuestion`` models (generator output before it is saved)."""
    from app.services.lms.mcq_utils import normalize_options, resolve_correct_option_index

    items = []
    for n, mcq in enumerate(mcqs or []):
        opts = normalize_options(
            [{"label": o.label, "text": o.text, "latex": getattr(o, "latex", None)} for o in mcq.options]
        )
        idx = resolve_correct_option_index(opts, mcq.correct_option_label)
        if idx is None:
            continue
        items.append({
            "question_text": mcq.question_text,
            "question_latex": getattr(mcq, "question_latex", None),
            "options": opts,
            "correct_option_index": idx,
            "_n": n,
        })
    keep = {q["_n"] for q in verify_mcq_keys(items)}
    return [m for n, m in enumerate(mcqs or []) if n in keep]
