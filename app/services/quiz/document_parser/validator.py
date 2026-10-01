"""Hard validation gate for :class:`MCQExtractionResult` (spec Step 10).

The pipeline runs this AFTER deterministic MCQ parsing and BEFORE persisting
anything user-visible. If validation reports a fatal error the caller must
raise an extraction failure rather than fall through to a silent LLM path.
This is what stops the class of bugs where "Q11" quietly shows up as "Q14"
or the correct-answer marker points at the wrong option.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence

from app.services.quiz.document_parser.mcq_from_canonical import (
    AnswerKeyEntry,
    MCQCandidate,
    MCQExtractionResult,
)


@dataclass
class ValidationIssue:
    """One validation finding. ``severity='error'`` blocks quiz creation."""

    code: str
    severity: str  # 'error' | 'warning'
    message: str
    source_question_number: int | None = None


@dataclass
class ValidationReport:
    """Aggregated validation result."""

    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def validate_extraction(
    result: MCQExtractionResult,
    *,
    require_answer_key: bool = True,
    expect_min_questions: int = 1,
) -> ValidationReport:
    """Run all checks and return an aggregated report.

    Fatal checks (errors):
        - fewer MCQs than ``expect_min_questions``
        - duplicate printed question numbers
        - any MCQ with != 4 options
        - any MCQ whose option order does not match A-D
        - key row referring to a question number not in the MCQ list
        - MCQ with no answer-key entry when ``require_answer_key`` is True
    Non-fatal (warnings):
        - gaps in the printed numbering sequence
        - MCQs whose parser attached ``warnings`` (e.g. ambiguous stem)
        - key entries with no matching MCQ (may be excluded on purpose)
    """
    issues: List[ValidationIssue] = []
    mcqs = list(result.mcqs)
    key = list(result.answer_key)

    _check_counts(mcqs, expect_min_questions, issues)
    _check_duplicates(mcqs, issues)
    _check_option_shape(mcqs, issues)
    _check_key_coverage(mcqs, key, require_answer_key, issues)
    _check_numbering_gaps(mcqs, issues)
    _bubble_parser_warnings(mcqs, issues)

    return ValidationReport(issues=issues)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_counts(
    mcqs: Sequence[MCQCandidate], minimum: int, out: List[ValidationIssue]
) -> None:
    if len(mcqs) < minimum:
        out.append(
            ValidationIssue(
                code="too_few_questions",
                severity="error",
                message=(
                    f"Extracted {len(mcqs)} question(s); at least {minimum} required"
                ),
            )
        )


def _check_duplicates(
    mcqs: Sequence[MCQCandidate], out: List[ValidationIssue]
) -> None:
    seen: dict[int, int] = {}
    for m in mcqs:
        seen[m.source_question_number] = seen.get(m.source_question_number, 0) + 1
    for n, count in seen.items():
        if count > 1:
            out.append(
                ValidationIssue(
                    code="duplicate_question_number",
                    severity="error",
                    message=f"Question number {n} appears {count} times",
                    source_question_number=n,
                )
            )


def _check_option_shape(
    mcqs: Sequence[MCQCandidate], out: List[ValidationIssue]
) -> None:
    for m in mcqs:
        labels = [o.label for o in m.options]
        if len(m.options) != 4:
            out.append(
                ValidationIssue(
                    code="wrong_option_count",
                    severity="error",
                    message=(
                        f"Question {m.source_question_number} has "
                        f"{len(m.options)} options (expected 4)"
                    ),
                    source_question_number=m.source_question_number,
                )
            )
            continue
        if labels != ["A", "B", "C", "D"]:
            out.append(
                ValidationIssue(
                    code="wrong_option_order",
                    severity="error",
                    message=(
                        f"Question {m.source_question_number} option labels "
                        f"{labels} are not A,B,C,D in order"
                    ),
                    source_question_number=m.source_question_number,
                )
            )
        for opt in m.options:
            body = (opt.text or opt.latex or "").strip()
            if not body:
                out.append(
                    ValidationIssue(
                        code="empty_option",
                        severity="error",
                        message=(
                            f"Question {m.source_question_number} option "
                            f"{opt.label} is empty"
                        ),
                        source_question_number=m.source_question_number,
                    )
                )


def _check_key_coverage(
    mcqs: Sequence[MCQCandidate],
    key: Sequence[AnswerKeyEntry],
    require_answer_key: bool,
    out: List[ValidationIssue],
) -> None:
    mcq_nums = {m.source_question_number for m in mcqs}
    key_by_num = {e.question_number: e for e in key}

    if require_answer_key:
        for m in mcqs:
            if m.source_question_number not in key_by_num:
                out.append(
                    ValidationIssue(
                        code="missing_answer_key",
                        severity="error",
                        message=(
                            f"Question {m.source_question_number} has no "
                            f"answer-key entry"
                        ),
                        source_question_number=m.source_question_number,
                    )
                )

    for entry in key:
        if entry.question_number not in mcq_nums:
            out.append(
                ValidationIssue(
                    code="orphan_answer_key",
                    severity="warning",
                    message=(
                        f"Answer key row for Q{entry.question_number} has no "
                        f"matching question"
                    ),
                    source_question_number=entry.question_number,
                )
            )


def _check_numbering_gaps(
    mcqs: Sequence[MCQCandidate], out: List[ValidationIssue]
) -> None:
    numbers = sorted(m.source_question_number for m in mcqs)
    if len(numbers) < 2:
        return
    lo, hi = numbers[0], numbers[-1]
    expected = set(range(lo, hi + 1))
    missing = sorted(expected - set(numbers))
    if missing:
        out.append(
            ValidationIssue(
                code="numbering_gap",
                severity="warning",
                message=(
                    f"Numbering gap in extracted questions: missing "
                    f"{missing[:5]}{'...' if len(missing) > 5 else ''}"
                ),
            )
        )


def _bubble_parser_warnings(
    mcqs: Sequence[MCQCandidate], out: List[ValidationIssue]
) -> None:
    for m in mcqs:
        for w in m.warnings:
            out.append(
                ValidationIssue(
                    code="parser_warning",
                    severity="warning",
                    message=w,
                    source_question_number=m.source_question_number,
                )
            )
