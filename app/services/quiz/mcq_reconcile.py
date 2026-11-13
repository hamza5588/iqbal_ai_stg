"""Verify / reconcile MCQs from static harvest vs AI extraction.

Prefer agreement; on conflict keep the static (printed) answer and options;
fill gaps from the AI arm. Does not claim mathematical 100% accuracy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from app.services.quiz.models import MCQQuestion

ExtractionBundle = Tuple[
    List[MCQQuestion],
    List[int],
    List[Optional[str]],
]


def _norm_text(value: Optional[str]) -> str:
    text = (value or "").lower()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[^\w\s\+\-\=\/\^\(\)\.]", "", text)
    return text


def _option_map(mcq: MCQQuestion) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for opt in mcq.options or []:
        label = (getattr(opt, "label", None) or "").strip().upper()
        if label in ("A", "B", "C", "D"):
            out[label] = _norm_text(getattr(opt, "text", None))
    return out


def _answers_agree(
    static_letter: Optional[str],
    ai_letter: Optional[str],
    static_mcq: MCQQuestion,
    ai_mcq: MCQQuestion,
) -> bool:
    s = (static_letter or static_mcq.correct_option_label or "").strip().upper()
    a = (ai_letter or ai_mcq.correct_option_label or "").strip().upper()
    return bool(s) and s == a


def _stems_similar(a: MCQQuestion, b: MCQQuestion) -> bool:
    na = _norm_text(a.question_text)
    nb = _norm_text(b.question_text)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Soft overlap for lightly rephrased AI stems.
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) < 12:
        return shorter in longer
    return shorter[:80] in longer or longer[:80] in shorter


def _options_compatible(static: MCQQuestion, ai: MCQQuestion) -> bool:
    sm = _option_map(static)
    am = _option_map(ai)
    if len(sm) < 4 or len(am) < 4:
        return False
    matches = 0
    for label in ("A", "B", "C", "D"):
        if sm.get(label) and sm[label] == am.get(label):
            matches += 1
    return matches >= 2


def _index_arm(
    questions: Sequence[MCQQuestion],
    source_numbers: Sequence[int],
    answer_letters: Sequence[Optional[str]],
) -> Dict[int, Tuple[MCQQuestion, Optional[str]]]:
    indexed: Dict[int, Tuple[MCQQuestion, Optional[str]]] = {}
    for idx, mcq in enumerate(questions):
        num = (
            source_numbers[idx]
            if idx < len(source_numbers)
            else idx + 1
        )
        try:
            key = int(num)
        except (TypeError, ValueError):
            key = idx + 1
        letter = answer_letters[idx] if idx < len(answer_letters) else None
        if key not in indexed:
            indexed[key] = (mcq, letter)
    return indexed


@dataclass
class ReconcileResult:
    questions: List[MCQQuestion] = field(default_factory=list)
    source_numbers: List[int] = field(default_factory=list)
    answer_letters: List[Optional[str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    agreed: int = 0
    static_only: int = 0
    ai_only: int = 0
    conflicts_static_wins: int = 0
    confidence: float = 0.9

    def as_bundle(self) -> ExtractionBundle:
        return self.questions, self.source_numbers, self.answer_letters


def reconcile_mcq_extractions(
    static: Optional[ExtractionBundle],
    ai: Optional[ExtractionBundle],
    *,
    wanted: Optional[int] = None,
) -> ReconcileResult:
    """Merge static + AI MCQ arms into one verified list."""
    result = ReconcileResult()
    if not static and not ai:
        result.warnings.append("Reconcile: both static and AI arms empty")
        result.confidence = 0.0
        return result

    if static and not ai:
        qs, nums, letters = static
        result.questions = list(qs)
        result.source_numbers = list(nums)
        result.answer_letters = list(letters)
        result.static_only = len(qs)
        result.warnings.append(
            f"Reconcile: AI arm empty — using static only ({len(qs)} MCQs)"
        )
        result.confidence = 0.92
        return _apply_wanted(result, wanted)

    if ai and not static:
        qs, nums, letters = ai
        result.questions = list(qs)
        result.source_numbers = list(nums)
        result.answer_letters = list(letters)
        result.ai_only = len(qs)
        result.warnings.append(
            f"Reconcile: static arm empty — using AI only ({len(qs)} MCQs)"
        )
        result.confidence = 0.88
        return _apply_wanted(result, wanted)

    assert static is not None and ai is not None
    static_map = _index_arm(*static)
    ai_map = _index_arm(*ai)
    all_nums = sorted(set(static_map) | set(ai_map))

    for num in all_nums:
        s_item = static_map.get(num)
        a_item = ai_map.get(num)
        if s_item and a_item:
            s_mcq, s_letter = s_item
            a_mcq, a_letter = a_item
            letter = (s_letter or s_mcq.correct_option_label or "").strip().upper() or None
            if _answers_agree(s_letter, a_letter, s_mcq, a_mcq) and (
                _stems_similar(s_mcq, a_mcq) or _options_compatible(s_mcq, a_mcq)
            ):
                kept = s_mcq.model_copy(
                    update={
                        "conversion_confidence": min(
                            0.99,
                            max(s_mcq.conversion_confidence, 0.95) + 0.02,
                        )
                    }
                )
                result.agreed += 1
            elif _answers_agree(s_letter, a_letter, s_mcq, a_mcq):
                kept = s_mcq.model_copy(
                    update={"conversion_confidence": max(s_mcq.conversion_confidence, 0.93)}
                )
                result.agreed += 1
            else:
                kept = s_mcq
                result.conflicts_static_wins += 1
                result.warnings.append(
                    f"Q{num}: answer conflict "
                    f"(static={(s_letter or s_mcq.correct_option_label)}, "
                    f"ai={(a_letter or a_mcq.correct_option_label)}); kept static"
                )
            result.questions.append(kept)
            result.source_numbers.append(num)
            result.answer_letters.append(letter or kept.correct_option_label)
        elif s_item:
            s_mcq, s_letter = s_item
            result.questions.append(s_mcq)
            result.source_numbers.append(num)
            result.answer_letters.append(s_letter or s_mcq.correct_option_label)
            result.static_only += 1
        else:
            assert a_item is not None
            a_mcq, a_letter = a_item
            result.questions.append(a_mcq)
            result.source_numbers.append(num)
            result.answer_letters.append(a_letter or a_mcq.correct_option_label)
            result.ai_only += 1

    total = len(result.questions) or 1
    agree_ratio = result.agreed / total
    result.confidence = min(
        0.99,
        0.90 + 0.08 * agree_ratio - 0.03 * (result.conflicts_static_wins / total),
    )
    result.warnings.append(
        "Reconcile: "
        f"agreed={result.agreed} static_only={result.static_only} "
        f"ai_only={result.ai_only} conflicts_static_wins={result.conflicts_static_wins} "
        f"total={len(result.questions)}"
    )
    return _apply_wanted(result, wanted)


def _apply_wanted(result: ReconcileResult, wanted: Optional[int]) -> ReconcileResult:
    if wanted is None or wanted <= 0 or len(result.questions) <= wanted:
        return result
    result.questions = result.questions[:wanted]
    result.source_numbers = result.source_numbers[:wanted]
    result.answer_letters = result.answer_letters[:wanted]
    result.warnings.append(f"Reconcile: truncated to first {wanted} MCQs")
    return result
