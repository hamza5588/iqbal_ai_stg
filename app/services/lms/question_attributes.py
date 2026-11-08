"""Cognitive level + difficulty of a question: the 3rd and 4th matching parameters.

Domain and Topic/Subtopic live in ``lms_question_taxonomy``. Cognitive level and
the *printed* difficulty live on ``questions`` (``cognitive_level``,
``printed_difficulty``). ``questions.difficulty`` is NOT NULL and silently
defaults to ``medium`` when a paper prints nothing, so a difficulty is only
treated as *known* when ``printed_difficulty`` is set - a defaulted ``medium``
must never count as a difficulty match.
"""
from __future__ import annotations

import json
import re
from typing import Optional

# Canonical cognitive levels used by the DIL papers (and analytics order).
COGNITIVE_LEVELS = ("Knowledge", "Understanding", "Application", "Analysis/Reasoning")
DIFFICULTY_LEVELS = ("easy", "medium", "hard")

_COGNITIVE_ALIASES = {
    "knowledge": "Knowledge",
    "remember": "Knowledge",
    "remembering": "Knowledge",
    "recall": "Knowledge",
    "understanding": "Understanding",
    "understand": "Understanding",
    "comprehension": "Understanding",
    "application": "Application",
    "apply": "Application",
    "applying": "Application",
    "analysis/reasoning": "Analysis/Reasoning",
    "analysis": "Analysis/Reasoning",
    "reasoning": "Analysis/Reasoning",
    "analysing": "Analysis/Reasoning",
    "analyzing": "Analysis/Reasoning",
}

_DIFFICULTY_ALIASES = {
    "easy": "easy", "e": "easy", "basic": "easy", "low": "easy", "simple": "easy", "beginner": "easy",
    "medium": "medium", "m": "medium", "intermediate": "medium", "moderate": "medium",
    "average": "medium", "avg": "medium",
    "hard": "hard", "h": "hard", "advanced": "hard", "difficult": "hard", "high": "hard",
    "challenging": "hard",
}

_EXPLANATION_COGNITIVE = re.compile(r"(?i)cognitive\s+level\s*:\s*([^;|\n]+)")


def normalize_cognitive_level(value: Optional[str]) -> Optional[str]:
    """'analysis / reasoning' -> 'Analysis/Reasoning'; unknown text -> None."""
    if not value or not str(value).strip():
        return None
    key = re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", str(value).strip().rstrip(" .").lower()))
    return _COGNITIVE_ALIASES.get(key)


def normalize_difficulty(value: Optional[str]) -> Optional[str]:
    """Printed label ('Intermediate', 'Basic', ...) -> easy|medium|hard, else None."""
    if not value or not str(value).strip():
        return None
    return _DIFFICULTY_ALIASES.get(str(value).strip().rstrip(" .").lower())


def cognitive_from_explanation(explanation: Optional[str]) -> Optional[str]:
    """Legacy storage: 'Cognitive level: Knowledge; Topic: ...' in ``explanation``."""
    m = _EXPLANATION_COGNITIVE.search(explanation or "")
    return normalize_cognitive_level(m.group(1)) if m else None


def cognitive_level_of(question) -> Optional[str]:
    """Canonical cognitive level of a Question row (column first, legacy text second)."""
    if question is None:
        return None
    return normalize_cognitive_level(getattr(question, "cognitive_level", None)) or cognitive_from_explanation(
        getattr(question, "explanation", None)
    )


def known_difficulty(question) -> Optional[str]:
    """easy|medium|hard only when the source paper printed a difficulty; else None."""
    if question is None or not getattr(question, "printed_difficulty", None):
        return None
    return normalize_difficulty(question.printed_difficulty) or (
        question.difficulty if question.difficulty in DIFFICULTY_LEVELS else None
    )


def from_bank_metadata(metadata_json: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(cognitive_level, printed_difficulty) recorded at Target-bank extraction time."""
    try:
        meta = json.loads(metadata_json or "{}") or {}
    except (TypeError, ValueError):
        return None, None
    cog = normalize_cognitive_level(meta.get("cognitive_level"))
    diff = (meta.get("printed_difficulty") or "").strip() or None
    return cog, diff
