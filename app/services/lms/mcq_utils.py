"""MCQ validation and normalization helpers."""
from __future__ import annotations

import json
import random
from typing import Any, List, Tuple

from app.services.lms.exceptions import LMSValidationError


LABELS = ("A", "B", "C", "D")


def normalize_options(raw_options: List[Any]) -> List[dict]:
    """Ensure options are dicts with labels A-D."""
    normalized: List[dict] = []
    for i, opt in enumerate(raw_options):
        if isinstance(opt, dict):
            label = opt.get("label") or LABELS[i]
            text = opt.get("text") or opt.get("option") or ""
            latex = opt.get("latex")
        else:
            label = LABELS[i]
            text = str(opt)
            latex = None
        normalized.append({"label": label, "text": text.strip(), "latex": latex})
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
            text = (opt.get("text") or opt.get("option") or "").strip().lower()
        else:
            text = str(opt).strip().lower()
        if not text:
            raise LMSValidationError("Option text cannot be empty")
        texts.append(text)
    if len(set(texts)) != 4:
        raise LMSValidationError("All 4 options must be unique")


def options_to_json(options: List[Any], correct_option_index: int) -> str:
    validate_mcq(options, correct_option_index)
    return json.dumps(normalize_options(options))


def options_from_json(options_json: str) -> List[dict]:
    return json.loads(options_json or "[]")


def shuffle_options(
    options: List[dict], correct_option_index: int
) -> Tuple[List[dict], int]:
    """Shuffle options and return new correct index."""
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
