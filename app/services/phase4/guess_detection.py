"""Heuristic guess detection (LLM optional later)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from app.services.phase4.constants import GUESS_MAX_DURATION_MS_EASY, GUESS_MAX_DURATION_MS_HARD


def detect_guess(
    *,
    duration_ms: Optional[int],
    difficulty: int,
    is_correct: bool,
    recent_answer_pattern: Optional[List[bool]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Returns (is_guess, signals_json-ready dict).
    Very fast + correct on hard Q suspicious; random-like pattern from recent attempts.
    """
    signals: Dict[str, Any] = {}
    guess = False
    d = max(1, min(5, int(difficulty)))
    max_ms = GUESS_MAX_DURATION_MS_HARD if d >= 4 else GUESS_MAX_DURATION_MS_EASY

    if duration_ms is not None and duration_ms >= 0 and duration_ms < max_ms:
        signals["fast_response"] = True
        if is_correct and d >= 4:
            guess = True
        if not is_correct and d <= 2:
            guess = True

    rp = recent_answer_pattern or []
    if len(rp) >= 6:
        # crude alternation detector
        alts = sum(1 for i in range(len(rp) - 1) if rp[i] != rp[i + 1])
        if alts >= len(rp) - 2:
            signals["alternating_pattern"] = True
            guess = True

    if len(rp) >= 4:
        # suspicious: mostly wrong on easy in window
        window = rp[-8:]
        if window.count(False) >= 5:
            easy_wrong_ratio = sum(1 for _ in window)  # placeholder
            signals["many_recent_wrongs"] = True
            if easy_wrong_ratio:
                pass

    return guess, signals


def pattern_randomness_score(choices: List[int]) -> float:
    """If multi-choice indices look uniform random, score high (0-1)."""
    if len(choices) < 5:
        return 0.0
    # simple entropy proxy
    uniq = len(set(choices))
    return max(0.0, min(1.0, 1.0 - (uniq - 1) / max(1, len(choices) - 1)))
