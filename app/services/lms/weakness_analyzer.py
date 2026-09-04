"""AI-powered weak-area detection from diagnostic question content."""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.lms_models import AssessmentAttempt, AttemptAnswer, Question
from app.services.lms.assessment_service import get_assessment
from app.services.lms.topic_resolver import get_or_create_topic_from_pdf_label
from app.utils.db import get_db
from app.utils.groq_rate_limit import invoke_with_groq_rate_limit
from app.utils.llm_factory import get_chat_model

logger = logging.getLogger(__name__)

WEAK_THRESHOLD = 60.0
_STRONG_THRESHOLD = 80.0


class WeakAreaItem(BaseModel):
    area_name: str = Field(..., description="Short student-friendly concept name (3-8 words)")
    score_percent: float = Field(..., ge=0.0, le=100.0)
    question_ids: List[int] = Field(default_factory=list)


class DiagnosticWeaknessResult(BaseModel):
    all_areas: List[WeakAreaItem] = Field(
        default_factory=list,
        description="EVERY learning area, each question_id in exactly one area (full coverage)",
    )
    weak_areas: List[WeakAreaItem] = Field(default_factory=list)
    strong_areas: List[WeakAreaItem] = Field(default_factory=list)


_WEAKNESS_PROMPT = """You analyze a student's diagnostic quiz to identify learning strengths and gaps.

Rules for area names:
- Use short SUBJECT TOPIC names only (e.g. "Fractions", "Algebra", "Geometry", "Linear Equations", "Cell Structure").
- Group related questions into 2-6 broad learning topics — NEVER one area per question.
- NEVER copy question wording (e.g. do NOT use "Simplify: 12/18" or "Solve: x^2 - 9 = 0" as area names).
- NEVER use PDF headings, document titles, section headers, or technical report names.
- NEVER use ALL CAPS titles or phrases like "IMPLEMENTATION REPORT", "FIXES MERGED", "PRS AWAITING".
- score_percent = round(100 * correct / total) for questions in that area.
- all_areas: EVERY area you identify, each question_id in exactly one area, so all_areas together cover 100% of the questions.
- weak_areas: the subset of all_areas where score_percent < 60
- strong_areas: the subset of all_areas where score_percent >= 80
- The same area object may appear in all_areas and (weak_areas or strong_areas).

Diagnostic title: {title}

Questions (id | result | text):
{questions_block}
"""


def _parse_assessment_meta(assessment) -> dict:
    if not assessment or not assessment.description:
        return {}
    try:
        meta = json.loads(assessment.description)
    except (json.JSONDecodeError, TypeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _save_assessment_meta(assessment, meta: dict) -> None:
    assessment.description = json.dumps(meta, ensure_ascii=False)
    get_db().commit()


def _question_concept_label(question: Question, meta: dict) -> Optional[str]:
    concepts = meta.get("question_concepts") or {}
    label = concepts.get(str(question.id))
    if label and str(label).strip():
        return str(label).strip()

    pdf_map = meta.get("question_pdf_topics") or {}
    pdf_label = pdf_map.get(str(question.id))
    if pdf_label and str(pdf_label).strip() and not _looks_like_document_heading(str(pdf_label)):
        return _humanize_label(str(pdf_label))

    return None


def _looks_like_question_text(text: str) -> bool:
    """True when label is likely copied from a question stem, not a topic."""
    t = (text or "").strip()
    if not t:
        return True
    lower = t.lower()
    question_starts = (
        "simplify",
        "solve",
        "multiply",
        "divide",
        "add",
        "subtract",
        "which",
        "what",
        "how",
        "find",
        "calculate",
        "evaluate",
        "identify",
        "choose",
        "select",
        "a ",
        "an ",
        "the ",
        "two ",
        "if ",
    )
    if any(lower.startswith(p) for p in question_starts):
        return True
    if re.search(r"[=+\-*/^]|x\s*=|x\^|\d+\s*/\s*\d+", t):
        return True
    if len(t.split()) > 7:
        return True
    return False


def _looks_like_document_heading(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if t.isupper() and len(t) > 12:
        return True
    boilerplate = (
        "implementation report",
        "merged to main",
        "awaiting review",
        "table of contents",
        "fix summary",
        "pull request",
        "changelog",
        "readme",
    )
    lower = t.lower()
    return any(p in lower for p in boilerplate)


def _humanize_label(label: str) -> str:
    text = re.sub(r"\s+", " ", label.strip())
    if text.isupper():
        text = text.title()
    text = re.sub(r"[^\w\s\-']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80] if text else "General Practice"


def _concept_from_question_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    for prefix in (
        r"^what is (?:the )?",
        r"^which (?:of the following )?",
        r"^how (?:does|do|can|would) ",
        r"^why (?:does|do|is|are) ",
        r"^identify (?:the )?",
        r"^choose (?:the )?",
        r"^select (?:the )?",
    ):
        cleaned = re.sub(prefix, "", cleaned, flags=re.I)
    cleaned = cleaned.rstrip("?.!")
    words = cleaned.split()[:8]
    label = " ".join(words).strip()
    if len(label) < 8:
        label = cleaned[:60].strip()
    return label[:80] or "Practice Area"


def _build_questions_block(questions: List[Question], ans_map: dict) -> str:
    lines = []
    for q in questions:
        ans = ans_map.get(q.id)
        result = "CORRECT" if ans and ans.is_correct else "WRONG"
        text = (q.question_text or "").replace("\n", " ")[:220]
        lines.append(f"- id={q.id} | {result} | {text}")
    return "\n".join(lines)


def _group_by_stored_concept(
    questions: List[Question],
    ans_map: dict,
    meta: dict,
) -> Optional[dict]:
    """Deterministic grouping when concept labels exist on questions."""
    groups: dict[str, dict] = {}
    for q in questions:
        label = _question_concept_label(q, meta)
        if not label:
            continue
        bucket = groups.setdefault(label, {"correct": 0, "total": 0, "question_ids": []})
        bucket["total"] += 1
        bucket["question_ids"].append(q.id)
        ans = ans_map.get(q.id)
        if ans and ans.is_correct:
            bucket["correct"] += 1

    if not groups or len(groups) >= max(3, len(questions) // 2):
        return None

    weak, strong = [], []
    for name, stats in groups.items():
        pct = round(100.0 * stats["correct"] / stats["total"], 2) if stats["total"] else 0.0
        topic = get_or_create_topic_from_pdf_label(name)
        entry = {
            "topic_id": topic.id if topic else 0,
            "topic_name": name,
            "score_percent": pct,
            "question_ids": stats["question_ids"],
        }
        if pct < WEAK_THRESHOLD:
            weak.append(entry)
        elif pct >= _STRONG_THRESHOLD:
            strong.append(entry)

    all_topics = []
    for name, stats in groups.items():
        pct = round(100.0 * stats["correct"] / stats["total"], 2) if stats["total"] else 0.0
        topic = get_or_create_topic_from_pdf_label(name)
        all_topics.append(
            {
                "topic_id": topic.id if topic else 0,
                "topic_name": name,
                "score_percent": pct,
                "question_ids": stats["question_ids"],
            }
        )

    weak.sort(key=lambda x: x["score_percent"])
    strong.sort(key=lambda x: x["score_percent"], reverse=True)
    return {"weak_topics": weak, "strong_topics": strong, "all_topics": all_topics}


def _ai_analyze_weakness(
    assessment,
    questions: List[Question],
    ans_map: dict,
) -> Optional[dict]:
    try:
        llm = get_chat_model(temperature=0.2, max_tokens=2048)
        structured = llm.with_structured_output(DiagnosticWeaknessResult)
        prompt = _WEAKNESS_PROMPT.format(
            title=assessment.title or "Diagnostic",
            questions_block=_build_questions_block(questions, ans_map),
        )
        result: DiagnosticWeaknessResult = invoke_with_groq_rate_limit(
            lambda: structured.invoke(prompt),
            description="diagnostic weakness analysis",
        )
    except Exception as exc:
        logger.warning("AI weakness analysis failed: %s", exc)
        return None

    def _to_entries(areas: List[WeakAreaItem]) -> List[dict]:
        entries = []
        for area in areas:
            name = _humanize_label(area.area_name)
            topic = get_or_create_topic_from_pdf_label(name)
            entries.append(
                {
                    "topic_id": topic.id if topic else 0,
                    "topic_name": name,
                    "score_percent": round(area.score_percent, 2),
                    "question_ids": area.question_ids,
                }
            )
        return entries

    all_entries = _to_entries(result.all_areas)
    weak_entries = _to_entries(result.weak_areas)
    strong_entries = _to_entries(result.strong_areas)
    if not all_entries:
        # Older/looser model output: reconstruct full coverage from weak+strong.
        seen: dict[str, dict] = {}
        for e in weak_entries + strong_entries:
            seen.setdefault(e["topic_name"], e)
        all_entries = list(seen.values())
    return {
        "weak_topics": weak_entries,
        "strong_topics": strong_entries,
        "all_topics": all_entries,
    }


def _cache_looks_invalid(cached: dict) -> bool:
    all_entries = (cached.get("weak_topics") or []) + (cached.get("strong_topics") or [])
    if not all_entries:
        return False
    bad = sum(
        1
        for e in all_entries
        if _looks_like_document_heading(e.get("topic_name") or "")
        or _looks_like_question_text(e.get("topic_name") or "")
    )
    return bad >= max(2, len(all_entries) // 2)


def _backfill_all_topics(cached: dict) -> dict:
    """Give pre-existing caches an ``all_topics`` list without re-running the LLM."""
    if cached.get("all_topics"):
        return cached
    seen: dict[str, dict] = {}
    for e in (cached.get("weak_topics") or []) + (cached.get("strong_topics") or []):
        seen.setdefault(e.get("topic_name") or "", e)
    cached["all_topics"] = list(seen.values())
    return cached


def _get_cached(assessment, attempt_id: int) -> Optional[dict]:
    meta = _parse_assessment_meta(assessment)
    cached = (meta.get("weakness_cache") or {}).get(str(attempt_id))
    if cached and isinstance(cached, dict):
        if _cache_looks_invalid(cached):
            return None
        return _backfill_all_topics(cached)
    return None


def _set_cache(assessment, attempt_id: int, result: dict) -> None:
    meta = _parse_assessment_meta(assessment)
    cache = meta.setdefault("weakness_cache", {})
    cache[str(attempt_id)] = result
    _save_assessment_meta(assessment, meta)


def analyze_diagnostic_attempt(attempt_id: int, use_cache: bool = True) -> dict:
    """
    Identify student-friendly weak/strong areas from diagnostic question content.
    Uses AI when needed; never shows raw PDF document headings.
    """
    db = get_db()
    attempt = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id).first()
    if not attempt:
        return {"weak_topics": [], "strong_topics": [], "all_topics": []}

    assessment = get_assessment(attempt.assessment_id)
    if use_cache:
        cached = _get_cached(assessment, attempt_id)
        if cached:
            return cached

    q_ids = [aq.question_id for aq in assessment.questions]
    questions = db.query(Question).filter(Question.id.in_(q_ids)).all()
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt_id).all()
    ans_map = {a.question_id: a for a in answers}
    meta = _parse_assessment_meta(assessment)

    grouped = _group_by_stored_concept(questions, ans_map, meta)
    if grouped:
        _set_cache(assessment, attempt_id, grouped)
        return grouped

    # AI groups questions into broad topics (Fractions, Algebra, etc.) — never per-question labels.
    if assessment.creation_mode in ("pdf_ai", "pdf_qa_auto", "mixed"):
        ai_result = _ai_analyze_weakness(assessment, questions, ans_map)
        if ai_result and (ai_result.get("weak_topics") or ai_result.get("strong_topics")):
            _set_cache(assessment, attempt_id, ai_result)
            return ai_result

    result = {"weak_topics": [], "strong_topics": [], "all_topics": []}
    _set_cache(assessment, attempt_id, result)
    return result
