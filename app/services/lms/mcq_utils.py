"""MCQ validation and normalization helpers."""
from __future__ import annotations

import json
import random
import re
from typing import Any, List, Optional, Tuple

from app.services.lms.exceptions import LMSValidationError
from app.services.quiz.math_text import recover_fields

LABELS = ("A", "B", "C", "D")

_LABEL_ONLY_RE = re.compile(r"^[\(\[]?[A-Da-d][\)\].:]?$")
_LABEL_PREFIX_RE = re.compile(
    r"^[\(\[]?([A-Da-d])(?:[\)\].:\-]\s+|\s+(?=\())"
)
_SMASHED_LATEX_RE = re.compile(r"[A-Za-z]{8,}")
_ANSWER_LETTER_RE = re.compile(
    r"^[\(\[]?([A-Da-d])[\)\]\.\:\-](?:\s+\S.*)?$"
)
_ANSWER_LETTER_PAREN_RE = re.compile(
    r"^[\(\[]?([A-Da-d])[\)\]]?\s+\(.*\)$"
)
_OPTION_LINE_RE = re.compile(r"^\s*[\(\[]?([A-Da-d])[\)\]\.\:\-](?:\s+(\S.*))?$")
_QUESTION_START_RE = re.compile(r"(?m)^([0-9]{1,3})[.)][ \t]+")
_ANSWER_KEY_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:(?:q(?:uestion)?|ans(?:wer)?)\s+)?([0-9]{1,3})[ \t]*[.\)\-:]*[ \t]*[\(\[]?([A-Da-d])[\)\]]?(?:[ \t]+\S[^\n]*)?[ \t]*$"
)
_ANSWER_KEY_NUM_RE = re.compile(r"^([0-9]{1,3})$")
_INLINE_ANSWER_RE = re.compile(
    r"(?im)^\s*(?:the\s+)?(?:correct\s+)?(?:answer|ans|sol(?:ution)?)(?=\s|[:.\-]|$)\s*(?:is\s*)?[:.\-]?\s*(.+?)\s*$"
)


def is_label_only(text: str) -> bool:
    return bool(_LABEL_ONLY_RE.fullmatch((text or "").strip()))


def strip_option_label_prefix(text: str) -> str:
    """Turn 'B (a repeating decimal)' into '(a repeating decimal)'."""
    raw = (text or "").strip()
    if not raw or is_label_only(raw):
        return raw
    stripped = _LABEL_PREFIX_RE.sub("", raw, count=1).strip()
    return stripped or raw


def is_broken_math_blob(text: str) -> bool:
    """True when a 'latex' field is actually an unspaced English sentence."""
    s = (text or "").strip()
    if not s or " " in s:
        return False
    return len(s) > 40 and bool(_SMASHED_LATEX_RE.search(s))


def pick_display_fields(text: Optional[str], latex: Optional[str]) -> Tuple[str, Optional[str]]:
    """Prefer reconstructed math latex over flattened PDF text like '4x2' or 'a3b2'."""
    text_s = strip_option_label_prefix(text or "")
    latex_s = strip_option_label_prefix(latex or "") or None
    if latex_s and is_broken_math_blob(latex_s):
        latex_s = None
    if is_label_only(text_s) and latex_s and not is_label_only(latex_s) and not is_broken_math_blob(latex_s):
        text_s, latex_s = recover_fields(latex_s, latex_s)
        return text_s, latex_s
    if latex_s and is_label_only(latex_s) and text_s and not is_label_only(text_s):
        latex_s = None
    return recover_fields(text_s, latex_s)


def parse_answer_label(text: str) -> Optional[str]:
    """Parse 'B', 'B.', '(B)', 'B (a repeating decimal)' into 'B'.

    Does not treat ordinary phrases like 'a repeating decimal' as the letter A.
    """
    s = (text or "").strip()
    if not s:
        return None
    if s.upper() in LABELS:
        return s.upper()
    match = _ANSWER_LETTER_RE.match(s) or _ANSWER_LETTER_PAREN_RE.match(s)
    if not match:
        return None
    return match.group(1).upper()


def sanitize_option(opt: dict, label: str) -> dict:
    text, latex = pick_display_fields(
        opt.get("text") or opt.get("option") or "",
        opt.get("latex") or None,
    )
    if is_label_only(text) and latex:
        text = latex
    return {"label": label, "text": text, "latex": latex}


def resolve_correct_option_index(options: List[Any], correct_label: Any) -> Optional[int]:
    """Match A–D without defaulting to index 0 (that made Q1 always 'correct')."""
    want = parse_answer_label(str(correct_label or "")) or str(correct_label or "").strip().upper()[:1]
    if want not in LABELS:
        return None
    for i, opt in enumerate(options):
        if isinstance(opt, dict):
            got = str(opt.get("label") or "").strip().upper()[:1]
        else:
            got = LABELS[i] if i < 4 else ""
        if got == want:
            return i
    return None


def normalize_options(raw_options: List[Any]) -> List[dict]:
    """Ensure options are dicts with labels A-D and cleaned text. Keeps original labels when valid."""
    normalized: List[dict] = []
    for i, opt in enumerate(raw_options):
        default_label = LABELS[i] if i < 4 else "A"
        if isinstance(opt, dict):
            label = str(opt.get("label") or default_label).strip().upper()[:1]
            if label not in LABELS:
                label = default_label
            normalized.append(sanitize_option(opt, label))
        else:
            normalized.append(sanitize_option({"text": str(opt), "label": default_label}, default_label))
    return normalized


def validate_mcq(options: List[Any], correct_option_index: int) -> None:
    """Validate MCQ structure; raises LMSValidationError on failure."""
    if len(options) != 4:
        raise LMSValidationError("MCQ must have exactly 4 options")
    if not (0 <= correct_option_index <= 3):
        raise LMSValidationError("correct_option_index must be between 0 and 3")
    texts = []
    for opt in options:
        if isinstance(opt, dict):
            text = (opt.get("text") or opt.get("option") or opt.get("latex") or "").strip()
        else:
            text = str(opt).strip()
        if not text:
            raise LMSValidationError("Option text cannot be empty")
        if is_label_only(text):
            raise LMSValidationError("Option text cannot be only A/B/C/D — use the full choice")
        texts.append(text.lower())
    if len(set(texts)) != 4:
        raise LMSValidationError("All 4 options must be unique")


def options_to_json(options: List[Any], correct_option_index: int) -> str:
    normalized = normalize_options(options)
    validate_mcq(normalized, correct_option_index)
    return json.dumps(normalized)


def options_from_json(options_json: str) -> List[dict]:
    return json.loads(options_json or "[]")


def shuffle_options(
    options: List[dict],
    correct_option_index: int,
    *,
    preserve_order: bool = False,
) -> Tuple[List[dict], int]:
    """Shuffle options and return new correct index.

    Native PDF MCQs keep A–D in paper order so the answer key still matches.
    """
    if preserve_order:
        kept = []
        for i, opt in enumerate(options):
            item = dict(opt)
            item["label"] = LABELS[i] if i < 4 else item.get("label") or "A"
            kept.append(item)
        return kept, correct_option_index
    indexed = list(enumerate(options))
    random.shuffle(indexed)
    shuffled = []
    new_correct = 0
    for new_idx, (old_idx, opt) in enumerate(indexed):
        item = dict(opt)
        item["label"] = LABELS[new_idx]
        shuffled.append(item)
        if old_idx == correct_option_index:
            new_correct = new_idx
    return shuffled, new_correct


def split_stem_and_options(text: str) -> Tuple[str, List[dict]]:
    """If a question stem contains A./B./C./D. lines, split them into options."""
    if not text:
        return "", []
    lines = text.replace("\r\n", "\n").split("\n")
    opts: List[dict] = []
    stem_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("--") and " of " in stripped:
            continue
        if _INLINE_ANSWER_RE.match(stripped):
            continue
        match = _OPTION_LINE_RE.match(stripped)
        if match:
            label = match.group(1).upper()
            body = strip_option_label_prefix((match.group(2) or "").strip())
            if body and is_label_only(body):
                if opts:
                    opts[-1]["text"] = (opts[-1]["text"] + " " + stripped).strip()
                else:
                    stem_lines.append(line)
                continue
            if any(o["label"] == label for o in opts):
                continue
            recovered_text, recovered_latex = recover_fields(body, None)
            opts.append({"label": label, "text": recovered_text, "latex": recovered_latex})
        elif opts:
            if stripped and not re.match(r"^(domain|unit|section|chapter)\b", stripped, re.I) and not re.search(r"answer key", stripped, re.I) and not _INLINE_ANSWER_RE.match(stripped):
                opts[-1]["text"] = (opts[-1]["text"] + " " + stripped).strip()
        else:
            stem_lines.append(line)
    labels = [o["label"] for o in opts if (o.get("text") or "").strip()]
    opts = [o for o in opts if (o.get("text") or "").strip()]
    if len(opts) == 4 and set(labels) == set(LABELS):
        opts.sort(key=lambda o: LABELS.index(o["label"]))
        return "\n".join(stem_lines).strip(), opts
    return text.strip(), []


def harvest_answer_key(pdf_text: str) -> List[Tuple[str, str]]:
    """Return (question_number, letter) pairs from an answer sheet / key section."""
    if not pdf_text:
        return []
    lower = pdf_text.lower()
    start = -1
    for marker in (
        "answer key",
        "answer sheet",
        "answers",
        "marking scheme",
        "correct answers",
        "question answer",
    ):
        idx = lower.rfind(marker)
        if idx != -1:
            start = max(start, idx)
    if start < 0:
        return []
    region = pdf_text[start:]
    found: List[Tuple[str, str]] = []
    seen = set()
    for match in _ANSWER_KEY_LINE_RE.finditer(region):
        num, letter = match.group(1), match.group(2).upper()
        if num in seen:
            continue
        seen.add(num)
        found.append((num, letter))

    # Table cells often land on separate lines: "1" / "D" / "Rational..."
    if len(found) < 4:
        lines = [ln.strip() for ln in region.splitlines()]
        i = 0
        extra: List[Tuple[str, str]] = []
        while i < len(lines) - 1:
            num_m = _ANSWER_KEY_NUM_RE.match(lines[i])
            if num_m:
                j = i + 1
                while j < len(lines) and not lines[j]:
                    j += 1
                if j < len(lines) and lines[j].upper() in LABELS and len(lines[j]) == 1:
                    extra.append((num_m.group(1), lines[j].upper()))
                    i = j + 1
                    continue
            i += 1
        for num, letter in extra:
            if num not in seen:
                seen.add(num)
                found.append((num, letter))
    return found


def peel_inline_answer(block: str) -> Tuple[str, Optional[str]]:
    """Pull 'Answer: B' / 'Ans. a repeating decimal' out of a question block. Last match wins."""
    if not block:
        return "", None
    kept: List[str] = []
    answer: Optional[str] = None
    for line in block.replace("\r\n", "\n").split("\n"):
        match = _INLINE_ANSWER_RE.match(line.strip())
        if match:
            raw = match.group(1).strip()
            if raw and raw.lower() not in ("key", "sheet", "keys"):
                answer = raw
            continue
        kept.append(line)
    return "\n".join(kept), answer


def harvest_native_mcqs(pdf_text: str) -> List[dict]:
    """Pull numbered A–D questions from a native MCQ paper (before the answer key)."""
    if not pdf_text:
        return []
    lower = pdf_text.lower()
    cut = len(pdf_text)
    for marker in ("answer key", "answer sheet", "marking scheme"):
        idx = lower.rfind(marker)
        if idx != -1:
            cut = min(cut, idx)
    body = pdf_text[:cut]
    starts = list(_QUESTION_START_RE.finditer(body))
    results: List[dict] = []
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(body)
        num = match.group(1)
        block, inline_answer = peel_inline_answer(body[match.end() : end])
        stem, opts = split_stem_and_options(block)
        if len(opts) != 4:
            continue
        stem_text, stem_latex = recover_fields(stem, None)
        results.append(
            {
                "number": int(num) if num.isdigit() else num,
                "text": stem_text,
                "latex": stem_latex,
                "options": opts,
                "answer": inline_answer,
            }
        )
    return results
