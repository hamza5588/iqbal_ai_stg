"""Infer curriculum section labels from structured PDF text (e.g. SECTION A: FRACTIONS)."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

SectionRange = Tuple[int, int, str]

_SECTION_RE = re.compile(
    r"SECTION\s+[A-Z]\s*:\s*([A-Za-z][A-Za-z\s&'-]*)\s*"
    r"\(Questions?\s*(\d+)\s*[-–—to]+\s*(\d+)\)",
    re.IGNORECASE,
)

_PART_RE = re.compile(
    r"PART\s+\d+\s*:\s*([A-Za-z][A-Za-z\s&'-]*)\s*"
    r"\(Questions?\s*(\d+)\s*[-–—to]+\s*(\d+)\)",
    re.IGNORECASE,
)


def _normalize_topic_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", (raw or "").strip())
    if name.isupper():
        name = name.title()
    return name[:80] or "General"


def parse_section_topics(pdf_text: str) -> List[SectionRange]:
    """Return (start_q, end_q, topic_name) ranges parsed from PDF headings."""
    text = pdf_text or ""
    sections: List[SectionRange] = []
    seen: set[SectionRange] = set()

    for pattern in (_SECTION_RE, _PART_RE):
        for match in pattern.finditer(text):
            name = _normalize_topic_name(match.group(1))
            start, end = int(match.group(2)), int(match.group(3))
            key = (start, end, name)
            if key not in seen:
                seen.add(key)
                sections.append(key)

    sections.sort(key=lambda s: s[0])
    return sections


def question_number_from_label(value) -> int:
    if isinstance(value, int):
        return value
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else 0


def topic_for_question_number(qnum: int, sections: List[SectionRange]) -> Optional[str]:
    if not qnum or not sections:
        return None
    for start, end, name in sections:
        if start <= qnum <= end:
            return name
    return None
