"""Exact question-number lookup in a lesson's uploaded document.

Students ask "solve question 92" / "q 58". Semantic search cannot answer that: every MCQ
looks alike to an embedding, and a question bank often prints the same number more than
once (two variants, a practice set and a test). The lesson chat used to hand the LLM the
whole document and let it guess - it picked one paper's question and the other paper's
answer-key letter.

This module resolves the number deterministically from the document text:

- ``load_document_pages``   page texts rebuilt from the stored RAG chunks (overlap removed,
                            running headers/footers dropped). The chunk table is the same
                            text the retriever serves, including the maths-aware hybrid text.
- ``parse_question_reference``  "question 92", "Q.58", "solve q 92 on page 20" -> (92, 20).
- ``find_question``         every occurrence of that printed number, with its page and the
                            answer-key entry of the SAME paper (the first key after it).

The caller decides what to do with 0 / 1 / many matches; nothing here calls an LLM.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Chunk text -> page text
# --------------------------------------------------------------------------------------


def merge_overlapping_texts(texts: Iterable[str]) -> str:
    """Join consecutive chunk texts, dropping only the overlap the splitter repeated.

    RecursiveCharacterTextSplitter repeats whole trailing pieces (lines / paragraphs) of a
    chunk at the head of the next one. Remove the longest run of leading lines of the new
    chunk that equals the trailing lines already kept (the first of them may be a partial
    line when a very long line was split mid-way). Lines that merely repeat elsewhere -
    "(A) 1" in two questions, "B" in an answer-key table - are content and are kept.
    """
    kept: List[str] = []
    for text in texts:
        lines = [ln.rstrip() for ln in str(text or "").splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            continue
        if not kept:
            kept.extend(lines)
            continue
        drop = _overlap_length(kept, lines)
        kept.extend(lines[drop:])
    return "\n".join(kept).strip()


def _overlap_length(kept: List[str], new: List[str]) -> int:
    """Number of leading lines of ``new`` already present at the end of ``kept``."""
    kept_cmp = [ln.strip() for ln in kept]
    new_cmp = [ln.strip() for ln in new]
    max_k = min(len(kept_cmp), len(new_cmp))
    for k in range(max_k, 0, -1):
        tail = kept_cmp[-k:]
        head = new_cmp[:k]
        if head[1:] != tail[1:]:
            continue
        first_new, first_kept = head[0], tail[0]
        if first_new == first_kept:
            return k
        # Overlap that started mid-line: the new chunk's first line is the end of a kept line.
        if first_new and len(first_new) >= 3 and first_kept.endswith(first_new):
            return k
    return 0


def _strip_running_lines(pages: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """Drop page headers/footers: short lines printed identically on most pages."""
    if len(pages) < 3:
        return pages
    counts: Counter = Counter()
    for _, text in pages:
        counts.update({ln.strip() for ln in text.splitlines() if ln.strip()})
    threshold = max(3, len(pages) // 4)
    # Only sentence-like lines: short repeated content ("(A) 1", "B") must never be dropped.
    running = {
        ln
        for ln, n in counts.items()
        if n >= threshold
        and 20 <= len(ln) <= 160
        and len(re.findall(r"[A-Za-z]{2,}", ln)) >= 3
        and not _QUESTION_START_RE.match(ln)
    }
    page_footer = re.compile(r"^page\s+\d+(\s+of\s+\d+)?$", re.I)
    out = []
    for page, text in pages:
        lines = [
            ln
            for ln in text.splitlines()
            if ln.strip() not in running and not page_footer.match(ln.strip())
        ]
        out.append((page, "\n".join(lines)))
    return out


def pages_from_chunk_rows(rows: Sequence[dict], *, strip_running: bool = True) -> List[Tuple[int, str]]:
    """[(1-indexed page, text)] from chunk rows {text, page (0-indexed), chunk_index}.

    ``strip_running`` drops page headers/footers (wanted for question lookup, not for the
    lesson text a teacher reads)."""
    ordered = sorted(rows, key=lambda r: (int(r.get("chunk_index") or 0), int(r.get("page") or 0)))
    by_page: Dict[int, List[str]] = {}
    prev_tail: Optional[str] = None
    for row in ordered:
        text = str(row.get("text") or "")
        if not text.strip():
            continue
        page = int(row.get("page") or 0) + 1
        bucket = by_page.setdefault(page, [])
        if prev_tail is not None and not bucket:
            # First chunk of a page: also strip overlap carried over from the previous page
            # (only the concatenated-split ingest path produces cross-page overlap).
            lines = text.splitlines()
            drop = _overlap_length(prev_tail.splitlines(), lines)
            text = "\n".join(lines[drop:])
        bucket.append(text)
        prev_tail = text
    pages = [(page, merge_overlapping_texts(texts)) for page, texts in sorted(by_page.items())]
    pages = [(p, t) for p, t in pages if t.strip()]
    return _strip_running_lines(pages) if strip_running else pages


def load_document_pages(thread_id: Optional[str], user_id: Optional[int]) -> List[Tuple[int, str]]:
    """Page texts of one uploaded document (scoped to its thread AND its owner)."""
    if not thread_id or not user_id:
        return []
    from app.utils.rag_vectorstore import query_all_chunks

    rows = query_all_chunks(str(thread_id), int(user_id))
    if not rows:
        logger.warning("document_questions: no chunks for thread_id=%s user_id=%s", thread_id, user_id)
        return []
    return pages_from_chunk_rows(rows)


def document_text_with_pages(pages: Sequence[Tuple[int, str]]) -> str:
    return "\n\n".join(f"[Page {page}]\n{text}" for page, text in pages)


def is_direct_upload_lesson_content(content: str) -> bool:
    """Content saved by "use uploaded PDF as the lesson" (lesson_routes)."""
    text = str(content or "")
    return "uploaded-doc-page" in text or ("Source document:" in text and "Page " in text)


def pages_from_lesson_content(content: str) -> List[Tuple[int, str]]:
    """Page texts from stored lesson content (fallback when no chunks are available)."""
    import html

    text = str(content or "")
    sections = re.findall(
        r'<section[^>]*data-page="(\d+)"[^>]*>(.*?)</section>', text, flags=re.S | re.I
    )
    if not sections:
        return [(1, text)] if text.strip() else []
    pages = []
    for page, body in sections:
        body = re.sub(r"<h2>\s*Page\s+\d+\s*</h2>", "", body, flags=re.I)
        body = re.sub(r"</(p|li|h\d)>|<br\s*/?>", "\n", body, flags=re.I)
        body = html.unescape(re.sub(r"<[^>]+>", "", body))
        pages.append((int(page), body.strip()))
    return pages


# --------------------------------------------------------------------------------------
# "question 92" in the student's message
# --------------------------------------------------------------------------------------

_REF_RE = re.compile(
    r"(?<![\w.])(?:"
    r"q(?:uestion|ues|stn|n|s)?\.?\s*(?:no\.?|num(?:ber)?\.?|#)?\s*[-:#]?\s*"
    r"|(?:mcq|item)\s*(?:no\.?|num(?:ber)?\.?|#)?\s*"
    r"|(?:sawal|swal|sawaal)\s*(?:no\.?|number|#)?\s*"
    r")0*(\d{1,3})(?!\d)(?![.,/^]\d)(?!\s*[\^/])",
    re.IGNORECASE,
)
_PAGE_RE = re.compile(r"\b(?:page|pg|p\.)\s*(?:no\.?|number)?\s*(\d{1,4})\b", re.IGNORECASE)


@dataclass(frozen=True)
class QuestionRef:
    number: int
    page: Optional[int] = None


def parse_question_reference(message: str) -> Optional[QuestionRef]:
    """The question number the student refers to, if exactly one is named."""
    text = str(message or "")
    numbers = {int(m.group(1)) for m in _REF_RE.finditer(text)}
    numbers.discard(0)
    if len(numbers) != 1:
        return None
    page_match = _PAGE_RE.search(text)
    page = int(page_match.group(1)) if page_match else None
    return QuestionRef(number=numbers.pop(), page=page)


# --------------------------------------------------------------------------------------
# Finding the question in the document
# --------------------------------------------------------------------------------------

# "92. Factorize", "Q58. Simplify", "**Q1.** Which", "31." (stem on the next line).
_QUESTION_START_RE = re.compile(
    r"^\s*(?:[*_#>|]+\s*)*(?:q(?:uestion)?\s*\.?\s*)?0*(\d{1,3})\s*[.):](?=\s|\*|$)",
    re.IGNORECASE,
)
# A key heading starts its line: "ANSWER KEY", "Master Answer Key & ...", "Answer Key (Variant B)".
_ANSWER_KEY_HEADER_RE = re.compile(
    r"^\W*(?:[A-Za-z]+\s+){0,2}(answer\s*key|answer\s*sheet|marking\s*scheme|correct\s*answers|answers)\b",
    re.IGNORECASE,
)
# "PART II: ...", "Unit 2: Logarithms", "Section B: ..." - ends the previous question's text.
_SECTION_HEADER_RE = re.compile(
    r"^\s*(?:[*_#>]+\s*)*(part|section|unit|chapter)\s+(?:[IVX]+|\d+|[A-Z])\b", re.IGNORECASE
)
_KEY_NUMBER_TOKEN_RE = re.compile(r"^(?:q\.?)?0*(\d{1,3})[.):]?$", re.IGNORECASE)
_KEY_LETTER_TOKEN_RE = re.compile(r"^\(?([A-Da-d])\)?[.:]?$")


@dataclass
class QuestionMatch:
    number: int
    page: int
    text: str
    answer_key: Optional[str] = None


@dataclass
class _Line:
    page: int
    text: str


def _flatten(pages: Sequence[Tuple[int, str]]) -> List[_Line]:
    lines: List[_Line] = []
    for page, text in pages:
        for raw in str(text or "").splitlines():
            if raw.strip():
                lines.append(_Line(page=int(page), text=raw.rstrip()))
    return lines


def _question_starts(lines: List[_Line]) -> List[Tuple[int, int]]:
    """[(line index, number)] of lines that start a numbered question.

    A numbered line only counts when its neighbour in the numbering also exists (n-1 among
    the few candidates before it or n+1 among the few after), so stray "5." lines in prose
    and a fraction denominator printed alone on a line ("3:") are ignored without hiding
    the real question next to them."""
    starts = []
    for i, ln in enumerate(lines):
        m = _QUESTION_START_RE.match(ln.text)
        if m:
            starts.append((i, int(m.group(1))))
    if len(starts) == 1:
        return starts
    window = 3
    accepted = []
    for pos, (i, n) in enumerate(starts):
        before = {m for _, m in starts[max(0, pos - window) : pos]}
        after = {m for _, m in starts[pos + 1 : pos + 1 + window]}
        if (n - 1) in before or (n + 1) in after:
            accepted.append((i, n))
    return accepted


def _key_boundaries(lines: List[_Line]) -> List[int]:
    return [
        i
        for i, ln in enumerate(lines)
        if _ANSWER_KEY_HEADER_RE.match(ln.text)
        and not _QUESTION_START_RE.match(ln.text)
        and len(ln.text.strip()) <= 120
    ]


def _answer_from_key(lines: List[_Line], key_start: int, key_end: int, number: int) -> Optional[str]:
    """Letter for ``number`` in the key section lines[key_start:key_end].

    Keys come as "92 A", "Q92 A", table rows "92 | A | U4 | A" or one cell per line. The
    entry is the tokens from the number up to the next number token; its answer is the
    LAST A-D letter (extra columns such as a cognitive-domain letter come before it)."""
    tokens: List[str] = []
    for ln in lines[key_start + 1 : key_end]:
        tokens.extend(t for t in re.split(r"[\s|]+", ln.text) if t and t not in ("*", "**"))
    tokens = [t.strip("*_") for t in tokens]
    for i, tok in enumerate(tokens):
        m = _KEY_NUMBER_TOKEN_RE.match(tok)
        if not m or int(m.group(1)) != number:
            continue
        letters = []
        # The entry also ends at a legend / prose line after the last row ("Legend: A = ...").
        for nxt in tokens[i + 1 : i + 9]:
            if _KEY_NUMBER_TOKEN_RE.match(nxt) or ":" in nxt or "=" in nxt:
                break
            lm = _KEY_LETTER_TOKEN_RE.match(nxt)
            if lm:
                letters.append(lm.group(1))
        # Upper-case cells win over a lower-case word such as the "a" in "Conjugate of a ...".
        upper = [ch for ch in letters if ch.isupper()]
        chosen = upper or letters
        return chosen[-1].upper() if chosen else None
    return None


def find_question(pages: Sequence[Tuple[int, str]], number: int) -> List[QuestionMatch]:
    """Every occurrence of printed question ``number`` in the document, in order."""
    lines = _flatten(pages)
    if not lines:
        return []
    starts = _question_starts(lines)
    keys = _key_boundaries(lines)
    start_idx = [i for i, _ in starts]
    matches: List[QuestionMatch] = []
    for pos, (i, n) in enumerate(starts):
        if n != number:
            continue
        end = start_idx[pos + 1] if pos + 1 < len(start_idx) else len(lines)
        later_keys = [k for k in keys if k > i]
        if later_keys and later_keys[0] < end:
            end = later_keys[0]
        body: List[str] = []
        for ln in lines[i:end]:
            if body and _SECTION_HEADER_RE.match(ln.text):
                break
            body.append(ln.text.strip())
        text = "\n".join(body).strip()
        if len(re.sub(r"\W", "", text)) < 4:
            continue
        answer = None
        if later_keys:
            key_start = later_keys[0]
            # The key of THIS paper ends where the next paper's numbering restarts.
            next_restart = next((j for j, m in starts if j > key_start and m == 1), len(lines))
            next_key = next((k for k in keys if k > key_start + 3), len(lines))
            answer = _answer_from_key(lines, key_start, min(next_restart, next_key), number)
        matches.append(QuestionMatch(number=number, page=lines[i].page, text=text, answer_key=answer))
    return _dedupe(matches)


def _dedupe(matches: List[QuestionMatch]) -> List[QuestionMatch]:
    seen = set()
    out = []
    for m in matches:
        key = re.sub(r"\s+", " ", m.text).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


# --------------------------------------------------------------------------------------
# Fixed replies (no LLM)
# --------------------------------------------------------------------------------------


def not_found_message(number: int, page: Optional[int] = None) -> str:
    where = f" on page {page}" if page else ""
    return (
        f"Question {number} could not be found{where} in the uploaded PDF. "
        "Please verify the question number or provide a screenshot."
    )


def _preview(text: str, limit: int = 140) -> str:
    lines = text.splitlines()
    first = re.sub(r"^\s*(?:[*_#>]+\s*)*(?:q(?:uestion)?\s*\.?\s*)?\d{1,3}\s*[.):]\s*", "", lines[0] if lines else "")
    stem = " ".join([first] + [ln for ln in lines[1:3] if not re.match(r"^\s*\(?[A-Da-d][).]", ln)]).strip()
    stem = re.sub(r"\s+", " ", stem)
    return stem if len(stem) <= limit else stem[: limit - 1].rstrip() + "…"


def ambiguous_message(number: int, matches: Sequence[QuestionMatch]) -> str:
    rows = "\n".join(f"- **Page {m.page}:** {_preview(m.text)}" for m in matches)
    example_page = matches[-1].page if matches else 1
    return (
        f"The uploaded PDF contains **{len(matches)} different questions numbered {number}**:\n\n"
        f"{rows}\n\n"
        f"Which one do you mean? Ask again with the page, for example: "
        f"\"question {number} on page {example_page}\"."
    )
