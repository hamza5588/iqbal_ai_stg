"""Grade level normalization and matching for class enrollment."""
from __future__ import annotations

import re
from typing import List, Optional

GRADE_OPTIONS: List[str] = [str(i) for i in range(1, 13)]

_WORD_TO_GRADE = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
    "eleventh": "11",
    "twelfth": "12",
}


def normalize_grade(value: Optional[str]) -> Optional[str]:
    """Normalize '8th', 'Grade 8', '8' → '8'."""
    if not value or not str(value).strip():
        return None
    raw = str(value).strip().lower()
    raw = raw.replace("grade", "").replace("class", "").replace("standard", "").strip()
    if raw in _WORD_TO_GRADE:
        return _WORD_TO_GRADE[raw]
    if raw.endswith(("th", "st", "nd", "rd")):
        raw = raw[:-2]
    match = re.search(r"(\d{1,2})", raw)
    if match:
        g = match.group(1)
        if g in GRADE_OPTIONS:
            return g
    return None


def grades_match(a: Optional[str], b: Optional[str]) -> bool:
    """True if both normalize to the same grade, or either is unset (legacy)."""
    na, nb = normalize_grade(a), normalize_grade(b)
    if na is None or nb is None:
        return True
    return na == nb


def parse_teaching_grades(value: Optional[str]) -> List[str]:
    """Parse teacher assigned grades: '8' or '8,9' or 'Grade 8'."""
    if not value or not str(value).strip():
        return []
    parts = re.split(r"[,;/|]+", str(value))
    grades: List[str] = []
    for part in parts:
        g = normalize_grade(part)
        if g and g not in grades:
            grades.append(g)
    if not grades:
        g = normalize_grade(value)
        if g:
            grades.append(g)
    return grades


def teacher_can_teach_grade(teacher_grade_field: Optional[str], class_grade: Optional[str]) -> bool:
    """Teacher may create/manage a class if class grade is in their assigned grades."""
    class_g = normalize_grade(class_grade)
    if class_g is None:
        return True
    assigned = parse_teaching_grades(teacher_grade_field)
    if not assigned:
        return True
    return class_g in assigned


def format_grade_label(grade: Optional[str]) -> str:
    g = normalize_grade(grade)
    if not g:
        return "General"
    suffix = "th"
    if g == "1":
        suffix = "st"
    elif g == "2":
        suffix = "nd"
    elif g == "3":
        suffix = "rd"
    return f"{g}{suffix} Grade"
