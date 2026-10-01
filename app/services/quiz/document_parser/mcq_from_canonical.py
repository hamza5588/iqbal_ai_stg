"""Deterministic MCQ parser that consumes a :class:`CanonicalDocument`.

This is the layer the pasted spec (Steps 5-6) calls for: MCQ identity comes
from the document's own numbering and structural boundaries, not from list
position or a regex-only pass over flattened text.

Contract:
    - Every returned MCQ carries the printed question number (``Q11`` stays
      ``Q11``), the printed A-D order, and back-pointers to the source
      blocks / pages it was assembled from.
    - Answer keys are joined by that printed number, never by list index.
    - When the document is ambiguous the parser reports the problem via
      :class:`MCQCandidate.warnings` so the pipeline's validator can gate
      on it - it never guesses silently.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from app.services.quiz.document_parser.canonical import (
    BlockType,
    CanonicalBlock,
    CanonicalDocument,
)

logger = logging.getLogger(__name__)


# Question-start recognizer. Kept intentionally strict: an optional
# ``Q``/``Question`` prefix (or nothing) then a small integer then ``.`` or
# ``)`` then whitespace. This is the ONLY numbering regex the new parser
# uses - and it operates per-block, not on the concatenated document text,
# so an inline "1) ... 2) ..." inside a math stem cannot be misread as a
# new question start (it lives inside a stem block, not on a block boundary).
_Q_HEAD_RE = re.compile(
    r"^\s*(?:q(?:uestion)?[ \t]*)?(?P<num>\d{1,3})[.)][ \t]+(?P<rest>\S.*)?$",
    re.IGNORECASE,
)

# Option-line recognizer. Same strict shape enforced in mcq_utils.
_OPTION_HEAD_RE = re.compile(
    r"^\s*[\(\[]?(?P<label>[A-Da-d])[\)\].:\-][ \t]+(?P<rest>\S.*)$"
)

# Answer-key row: "8 D ..." or "Q8. D" or "8) D".
_ANSWER_KEY_ROW_RE = re.compile(
    r"^\s*(?:q(?:uestion)?[ \t]*)?(?P<num>\d{1,3})"
    r"[ \t]*[.\)\-:]?[ \t]+[\(\[]?(?P<letter>[A-Da-d])[\)\]]?"
    r"(?:[ \t]+\S.*)?$",
    re.IGNORECASE,
)

_ANSWER_KEY_HEADER_RE = re.compile(
    r"(?i)\b(answer\s*key|answer\s*sheet|marking\s*scheme|correct\s*answers?)\b"
)

_LABELS = ("A", "B", "C", "D")


@dataclass
class MCQOption:
    """One MCQ option carrying its source blocks for traceability."""

    label: str
    text: str
    latex: Optional[str] = None
    source_block_ids: List[str] = field(default_factory=list)


@dataclass
class MCQCandidate:
    """One MCQ extracted from the canonical document.

    ``source_question_number`` is what the pipeline must persist - never
    ``idx + 1``. When the printed label is non-numeric (rare: "Q1a"), the
    parser keeps the raw string here so downstream reporting still points
    at the correct source location."""

    source_question_number: int
    printed_label: str
    stem: str
    stem_latex: Optional[str]
    options: List[MCQOption]
    source_page: int
    source_block_ids: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class AnswerKeyEntry:
    """One row of an answer key section."""

    question_number: int
    letter: str  # 'A' | 'B' | 'C' | 'D'
    source_block_id: Optional[str] = None
    source_page: Optional[int] = None


@dataclass
class MCQExtractionResult:
    """Everything the pipeline needs from a document. Immutable-ish DTO."""

    mcqs: List[MCQCandidate]
    answer_key: List[AnswerKeyEntry]
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_mcqs(document: CanonicalDocument) -> MCQExtractionResult:
    """Extract MCQs and (optional) answer-key rows from a canonical document.

    The extraction does two passes over the block stream:
        1. Split into "body" (before any answer-key header) and "key"
           (from the header onward). Papers without a key section route
           everything through the body pass.
        2. Walk the body block by block; a block whose text starts with a
           question head opens an MCQ, subsequent option-head blocks fill
           its options in order, and any other block extends the currently
           open field (stem before options, current option after)."""
    body_blocks, key_blocks = _split_body_and_key(document)

    mcqs = _extract_mcqs_from_body(body_blocks)
    answer_key = _extract_answer_key(key_blocks)

    warnings: List[str] = []
    if mcqs and not answer_key:
        warnings.append("No answer key section detected")
    return MCQExtractionResult(mcqs=mcqs, answer_key=answer_key, warnings=warnings)


# ---------------------------------------------------------------------------
# Body / key split
# ---------------------------------------------------------------------------

def _split_body_and_key(
    document: CanonicalDocument,
) -> tuple[List[CanonicalBlock], List[CanonicalBlock]]:
    """Return (body, key) block lists. Key is empty when no header is found."""
    all_blocks: List[CanonicalBlock] = []
    for page in document.pages:
        for blk in page.blocks:
            if blk.extra.get("is_metadata_banner"):
                continue
            if blk.block_type == BlockType.HEADER_FOOTER:
                continue
            all_blocks.append(blk)

    for idx, blk in enumerate(all_blocks):
        if _ANSWER_KEY_HEADER_RE.search(blk.text or ""):
            return all_blocks[:idx], all_blocks[idx:]
    return all_blocks, []


# ---------------------------------------------------------------------------
# Body pass: build MCQs from blocks
# ---------------------------------------------------------------------------

def _extract_mcqs_from_body(blocks: Sequence[CanonicalBlock]) -> List[MCQCandidate]:
    mcqs: List[MCQCandidate] = []
    current: Optional[MCQCandidate] = None
    current_option: Optional[MCQOption] = None
    seen_numbers: Dict[int, MCQCandidate] = {}

    def _flush() -> None:
        nonlocal current, current_option
        if current is None:
            return
        _finalize(current)
        # De-duplicate: the printed number wins. Duplicate numbers are a
        # sign of a malformed PDF; we keep the first occurrence and warn.
        existing = seen_numbers.get(current.source_question_number)
        if existing is None:
            seen_numbers[current.source_question_number] = current
            mcqs.append(current)
        else:
            existing.warnings.append(
                f"Duplicate question number {current.source_question_number} "
                f"encountered on page {current.source_page} - kept the first"
            )
        current = None
        current_option = None

    for blk in blocks:
        text = (blk.text or "").strip()
        if not text and not blk.latex:
            continue

        q_head = _Q_HEAD_RE.match(text) if text else None
        opt_head = _OPTION_HEAD_RE.match(text) if text else None

        if q_head:
            _flush()
            try:
                num = int(q_head.group("num"))
            except (TypeError, ValueError):
                continue
            rest = (q_head.group("rest") or "").strip()
            current = MCQCandidate(
                source_question_number=num,
                printed_label=q_head.group(0).split(rest)[0].strip() if rest else text,
                stem=rest,
                stem_latex=blk.latex if not rest else None,
                options=[],
                source_page=blk.page_number,
                source_block_ids=[blk.block_id],
            )
            current_option = None
            continue

        if opt_head and current is not None:
            label = opt_head.group("label").upper()
            rest = (opt_head.group("rest") or "").strip()
            current_option = MCQOption(
                label=label,
                text=rest,
                latex=blk.latex,
                source_block_ids=[blk.block_id],
            )
            current.options.append(current_option)
            current.source_block_ids.append(blk.block_id)
            continue

        # Continuation line.
        if current is None:
            continue
        current.source_block_ids.append(blk.block_id)
        if current_option is not None:
            current_option.text = _join_wrap(current_option.text, text)
            if blk.latex and not current_option.latex:
                current_option.latex = blk.latex
        else:
            current.stem = _join_wrap(current.stem, text)
            if blk.latex and not current.stem_latex:
                current.stem_latex = blk.latex

    _flush()
    return mcqs


def _finalize(mcq: MCQCandidate) -> None:
    """Post-process a completed MCQ: sanity checks + warnings."""
    if len(mcq.options) != 4:
        mcq.warnings.append(
            f"Question {mcq.source_question_number} has {len(mcq.options)} "
            f"options (expected 4)"
        )
    labels = [o.label for o in mcq.options]
    if labels != list(_LABELS[: len(labels)]):
        mcq.warnings.append(
            f"Question {mcq.source_question_number} option order {labels} "
            f"does not match A-D"
        )
    for opt in mcq.options:
        opt.text = opt.text.strip()
        if opt.latex:
            opt.latex = opt.latex.strip()
    mcq.stem = mcq.stem.strip()
    if mcq.stem_latex:
        mcq.stem_latex = mcq.stem_latex.strip()


def _join_wrap(prefix: str, addition: str) -> str:
    """Join a wrapped-line continuation. Empty prefix keeps the addition."""
    prefix = (prefix or "").rstrip()
    addition = (addition or "").strip()
    if not addition:
        return prefix
    if not prefix:
        return addition
    if prefix.endswith("-"):
        # Soft hyphen wrap: "polyno-\nmial" -> "polynomial".
        return prefix[:-1] + addition
    return prefix + " " + addition


# ---------------------------------------------------------------------------
# Answer-key pass
# ---------------------------------------------------------------------------

def _extract_answer_key(blocks: Sequence[CanonicalBlock]) -> List[AnswerKeyEntry]:
    """Read answer-key rows from the tail of the document.

    Table layouts in PDFs sometimes place "1" and "C" on adjacent blocks
    instead of the same line. The two-pass strategy first tries whole-line
    matches, then falls back to (numeric block) + (single-letter block)
    pairs when the first pass did not find enough answers."""
    if not blocks:
        return []

    # Skip the block that carried the header itself so its text does not
    # accidentally re-match as a data row (rare, but observed on
    # "Answer Key and Assessment Mapping" preambles).
    data_blocks: List[CanonicalBlock] = []
    header_seen = False
    for blk in blocks:
        if not header_seen and _ANSWER_KEY_HEADER_RE.search(blk.text or ""):
            header_seen = True
            continue
        data_blocks.append(blk)

    seen: Dict[int, AnswerKeyEntry] = {}

    # Pass 1: whole-line match.
    for blk in data_blocks:
        line = (blk.text or "").strip()
        if not line:
            continue
        m = _ANSWER_KEY_ROW_RE.match(line)
        if not m:
            continue
        try:
            num = int(m.group("num"))
        except (TypeError, ValueError):
            continue
        letter = m.group("letter").upper()
        if letter not in _LABELS:
            continue
        if num in seen:
            continue
        seen[num] = AnswerKeyEntry(
            question_number=num,
            letter=letter,
            source_block_id=blk.block_id,
            source_page=blk.page_number,
        )

    # Pass 2: table-cell layout ("1" then "C" on adjacent blocks).
    for i in range(len(data_blocks) - 1):
        left = (data_blocks[i].text or "").strip()
        right = (data_blocks[i + 1].text or "").strip()
        if not left.isdigit() or len(left) > 3:
            continue
        if len(right) != 1 or right.upper() not in _LABELS:
            continue
        num = int(left)
        if num in seen:
            continue
        seen[num] = AnswerKeyEntry(
            question_number=num,
            letter=right.upper(),
            source_block_id=data_blocks[i + 1].block_id,
            source_page=data_blocks[i + 1].page_number,
        )

    return sorted(seen.values(), key=lambda e: e.question_number)


# ---------------------------------------------------------------------------
# Answer-key join by source number (NOT by index)
# ---------------------------------------------------------------------------

def apply_answer_key(
    mcqs: Sequence[MCQCandidate],
    answer_key: Sequence[AnswerKeyEntry],
) -> Dict[int, str]:
    """Return ``{source_question_number: correct_letter}``.

    The mapping is keyed on the printed number for both sides. Any MCQ with
    no matching key entry, or any key entry with no matching MCQ, is
    reported by the caller's validator - this function itself just returns
    the intersection so higher layers can decide how strict to be."""
    keyed: Dict[int, str] = {e.question_number: e.letter for e in answer_key}
    out: Dict[int, str] = {}
    for mcq in mcqs:
        letter = keyed.get(mcq.source_question_number)
        if letter and letter in _LABELS:
            out[mcq.source_question_number] = letter
    return out
