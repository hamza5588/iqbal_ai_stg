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
    from app.services.quiz.concept_labeler import is_generic_concept

    concepts = meta.get("question_concepts") or {}
    label = concepts.get(str(question.id))
    if label and str(label).strip() and not is_generic_concept(str(label)):
        return str(label).strip()

    pdf_map = meta.get("question_pdf_topics") or {}
    pdf_label = pdf_map.get(str(question.id))
    if (
        pdf_label
        and str(pdf_label).strip()
        and not _looks_like_document_heading(str(pdf_label))
        and not is_generic_concept(str(pdf_label))
    ):
        return _humanize_label(str(pdf_label))

    return None


def _group_by_stored_concept(
    questions: List[Question],
    ans_map: dict,
    meta: dict,
) -> Optional[dict]:
    """Deterministic grouping when real concept labels exist on most questions."""
    groups: dict[str, dict] = {}
    labeled = 0
    for q in questions:
        label = _question_concept_label(q, meta)
        if not label:
            continue
        labeled += 1
        bucket = groups.setdefault(label, {"correct": 0, "total": 0, "question_ids": []})
        bucket["total"] += 1
        bucket["question_ids"].append(q.id)
        ans = ans_map.get(q.id)
        if ans and ans.is_correct:
            bucket["correct"] += 1

    # Need multiple real topics covering most of the paper — otherwise let AI group.
    min_coverage = max(3, int(0.6 * len(questions))) if questions else 0
    if len(groups) < 2 or labeled < min_coverage:
        return None
    if len(groups) >= max(3, len(questions) // 2):
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
        from app.services.quiz.concept_labeler import is_generic_concept

        entries = []
        for area in areas:
            name = _humanize_label(area.area_name)
            if is_generic_concept(name):
                continue
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


def _recompute_area_scores(entries: List[dict], ans_map: dict) -> List[dict]:
    """Recalculate each area's % from real attempt answers (ignore LLM-reported %)."""
    out = []
    for entry in entries:
        qids = [int(q) for q in (entry.get("question_ids") or []) if int(q) in ans_map]
        if not qids:
            continue
        correct = sum(1 for qid in qids if ans_map.get(qid) and ans_map[qid].is_correct)
        pct = round(100.0 * correct / len(qids), 2)
        topic = get_or_create_topic_from_pdf_label(entry.get("topic_name") or "Practice")
        out.append(
            {
                "topic_id": topic.id if topic else int(entry.get("topic_id") or 0),
                "topic_name": entry.get("topic_name") or "Practice",
                "score_percent": pct,
                "question_ids": qids,
                "correct": correct,
                "total": len(qids),
            }
        )
    return out


def _keyword_concept(text: str) -> Optional[str]:
    """Cheap topic guess so orphan questions still count toward mastery."""
    t = (text or "").lower()
    rules = (
        ("Profit and Loss", ("profit", "loss", "discount", "marked price", "shopkeeper")),
        ("Percentages", ("percent", "percentage")),
        ("Fractions", ("fraction", "numerator", "denominator")),
        ("Linear Equations", ("equation", "solve for")),
        ("Inequalities", ("inequalit", "greater than", "less than")),
        ("Exponents", ("expon", "power of", "squared", "cubed")),
        ("Polynomials and Factoring", ("polynomial", "factor", "quadratic")),
        ("Rational and Irrational Numbers", ("rational", "irrational", "terminating", "recurring")),
        ("Sequences and Series", ("sequence", "nth term", "arithmetic sequence", "series")),
        ("Set Theory", ("union", "intersection", "subset", "roster")),
        ("Work and Rate Problems", ("together can", "days to complete", "work rate")),
        ("Algebraic Expressions", ("expand", "coefficient", "expression equal")),
    )
    for name, keys in rules:
        if any(k in t for k in keys):
            return name
    if "%" in (text or ""):
        return "Percentages"
    return None


def _ensure_full_question_coverage(
    analysis: dict,
    questions: List[Question],
    ans_map: dict,
    meta: dict,
) -> dict:
    """Every attempt question must sit in exactly one topic so Topic Mastery
    matches overall diagnostic % (weighted correct/total)."""
    from app.services.quiz.concept_labeler import is_generic_concept

    all_q_ids = [q.id for q in questions]
    q_by_id = {q.id: q for q in questions}
    entries = list(analysis.get("all_topics") or [])
    if not entries:
        entries = list(analysis.get("weak_topics") or []) + list(analysis.get("strong_topics") or [])

    # First-wins ownership so a question is never double-counted.
    owned: set[int] = set()
    cleaned: List[dict] = []
    for entry in entries:
        name = (entry.get("topic_name") or "").strip()
        if not name or is_generic_concept(name):
            continue
        keep = []
        for qid in entry.get("question_ids") or []:
            qid = int(qid)
            if qid in owned or qid not in q_by_id:
                continue
            owned.add(qid)
            keep.append(qid)
        if keep:
            cleaned.append({**entry, "topic_name": name, "question_ids": keep})

    orphans = [qid for qid in all_q_ids if qid not in owned]
    by_name = {e["topic_name"]: e for e in cleaned}
    for qid in orphans:
        q = q_by_id[qid]
        label = _question_concept_label(q, meta)
        if not label or is_generic_concept(label):
            label = _keyword_concept(q.question_text or "") or "Algebra Basics"
        label = _humanize_label(label)
        if is_generic_concept(label):
            label = "Algebra Basics"
        bucket = by_name.get(label)
        if not bucket:
            bucket = {
                "topic_id": 0,
                "topic_name": label,
                "question_ids": [],
                "score_percent": 0.0,
            }
            cleaned.append(bucket)
            by_name[label] = bucket
        bucket["question_ids"].append(qid)

    cleaned = _recompute_area_scores(cleaned, ans_map)
    weak = [e for e in cleaned if e["score_percent"] < WEAK_THRESHOLD]
    strong = [e for e in cleaned if e["score_percent"] >= _STRONG_THRESHOLD]
    weak.sort(key=lambda x: x["score_percent"])
    strong.sort(key=lambda x: x["score_percent"], reverse=True)
    return {"weak_topics": weak, "strong_topics": strong, "all_topics": cleaned}


def _analysis_covers_all_questions(analysis: dict, q_ids: List[int]) -> bool:
    covered: set[int] = set()
    for e in analysis.get("all_topics") or []:
        covered.update(int(x) for x in (e.get("question_ids") or []))
    return bool(q_ids) and set(q_ids).issubset(covered)
    from app.services.quiz.concept_labeler import is_generic_concept

    all_entries = (
        (cached.get("weak_topics") or [])
        + (cached.get("strong_topics") or [])
        + (cached.get("all_topics") or [])
    )
    if not all_entries:
        return False
    names = [(e.get("topic_name") or "").strip() for e in all_entries]
    # Only "General" (or similar) — force a fresh AI / topic pass.
    if names and all(is_generic_concept(n) or not n for n in names):
        return True
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


def _rebind_topic_ids(result: dict) -> tuple[dict, bool]:
    """Re-map area topic_ids from topic_name via the current resolver.

    Older caches may share one topic_id across several distinct area names
    because the resolver used to merge on substring/subset. Rebinding keeps
    student-visible names and teacher mastery rows 1:1 with those areas.
    """
    changed = False
    for key in ("weak_topics", "strong_topics", "all_topics"):
        entries = result.get(key) or []
        for entry in entries:
            name = (entry.get("topic_name") or "").strip()
            if not name:
                continue
            topic = get_or_create_topic_from_pdf_label(name)
            if not topic:
                continue
            if int(entry.get("topic_id") or 0) != int(topic.id):
                entry["topic_id"] = topic.id
                changed = True
    return result, changed


def _get_cached(assessment, attempt_id: int) -> Optional[dict]:
    meta = _parse_assessment_meta(assessment)
    cached = (meta.get("weakness_cache") or {}).get(str(attempt_id))
    if cached and isinstance(cached, dict):
        if _cache_looks_invalid(cached):
            return None
        cached = _backfill_all_topics(cached)
        cached, rebound = _rebind_topic_ids(cached)
        if rebound:
            _set_cache(assessment, attempt_id, cached)
        return cached
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
    Every attempt question is assigned to exactly one topic so Topic Mastery
    (weighted) matches the overall diagnostic score.
    """
    db = get_db()
    attempt = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id).first()
    if not attempt:
        return {"weak_topics": [], "strong_topics": [], "all_topics": []}

    assessment = get_assessment(attempt.assessment_id)
    from app.services.lms.attempt_service import attempt_question_ids

    q_ids = attempt_question_ids(attempt)
    questions = db.query(Question).filter(Question.id.in_(q_ids)).all()
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt_id).all()
    ans_map = {a.question_id: a for a in answers}
    meta = _parse_assessment_meta(assessment)

    if use_cache:
        cached = _get_cached(assessment, attempt_id)
        if cached and _analysis_covers_all_questions(cached, q_ids):
            # Still recompute scores from live answers + fill any edge orphans.
            fixed = _ensure_full_question_coverage(cached, questions, ans_map, meta)
            if fixed != cached:
                _set_cache(assessment, attempt_id, fixed)
            return fixed

    grouped = _group_by_stored_concept(questions, ans_map, meta)
    if grouped:
        result = _ensure_full_question_coverage(grouped, questions, ans_map, meta)
        _set_cache(assessment, attempt_id, result)
        return result

    ai_result = _ai_analyze_weakness(assessment, questions, ans_map)
    if ai_result and (
        ai_result.get("all_topics")
        or ai_result.get("weak_topics")
        or ai_result.get("strong_topics")
    ):
        result = _ensure_full_question_coverage(ai_result, questions, ans_map, meta)
        _set_cache(assessment, attempt_id, result)
        return result

    topic_result = _group_by_question_topic_id(questions, ans_map, assessment)
    if topic_result and (topic_result.get("weak_topics") or topic_result.get("strong_topics") or topic_result.get("all_topics")):
        result = _ensure_full_question_coverage(topic_result, questions, ans_map, meta)
        _set_cache(assessment, attempt_id, result)
        return result

    # Last resort: put every question in one keyword/concept bucket each.
    result = _ensure_full_question_coverage(
        {"weak_topics": [], "strong_topics": [], "all_topics": []},
        questions,
        ans_map,
        meta,
    )
    _set_cache(assessment, attempt_id, result)
    return result


def _group_by_question_topic_id(
    questions: List[Question],
    ans_map: dict,
    assessment,
) -> Optional[dict]:
    """Fallback grouping by questions.topic_id, mirroring the quiz branch of
    performance_service.analyze_attempt (same resolver + same thresholds),
    for use when diagnostic concept metadata is absent/empty."""
    from app.services.lms.performance_service import (
        _resolve_question_topic_id,
        _topic_display_name,
    )

    by_topic: dict[int, dict] = {}
    for q in questions:
        topic_id = _resolve_question_topic_id(q, assessment)
        if not topic_id:
            continue
        bucket = by_topic.setdefault(
            topic_id, {"topic_id": topic_id, "correct": 0, "total": 0, "question_ids": []}
        )
        bucket["total"] += 1
        bucket["question_ids"].append(q.id)
        ans = ans_map.get(q.id)
        if ans and ans.is_correct:
            bucket["correct"] += 1

    if not by_topic:
        return None

    weak, strong, all_topics = [], [], []
    for tid, stats in by_topic.items():
        pct = round(100.0 * stats["correct"] / stats["total"], 2) if stats["total"] else 0.0
        entry = {
            "topic_id": tid,
            "topic_name": _topic_display_name(tid),
            "score_percent": pct,
            "question_ids": stats["question_ids"],
        }
        all_topics.append(entry)
        if pct < WEAK_THRESHOLD:
            weak.append(entry)
        elif pct >= _STRONG_THRESHOLD:
            strong.append(entry)

    weak.sort(key=lambda x: x["score_percent"])
    strong.sort(key=lambda x: x["score_percent"], reverse=True)
    return {"weak_topics": weak, "strong_topics": strong, "all_topics": all_topics}
