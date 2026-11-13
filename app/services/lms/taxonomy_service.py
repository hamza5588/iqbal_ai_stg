"""Canonical Domain -> Subtopic taxonomy shared by Baseline, Follow-up, Target PDFs,
Learning Chat, Guided Practice and analytics.

* A **Domain** is the existing ``topics`` row the paper's printed Domain resolves
  to (``topic_resolver.get_or_create_topic_from_pdf_label``) - the same id every
  Domain score already uses, so nothing Domain-level changes.
* A **Subtopic** (``lms_subtopics``) is the printed ``Topic:`` of a question,
  scoped to (Domain, grade). Names match after normalisation (case, spacing,
  punctuation, "&"/"and", simple plurals) or through an alias an admin confirmed.
  Nothing is fuzzy-merged automatically: "Common Logarithms" and "Exponential and
  Logarithmic Forms" stay two subtopics until an admin maps one onto the other.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, Iterable, List, Optional

from app.models.lms_models import (
    Question,
    QuestionTaxonomy,
    StudentSubtopicScore,
    Subtopic,
    SubtopicEvidence,
    Topic,
)
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.utils.db import get_db

logger = logging.getLogger(__name__)

_PRINTED_TOPIC_RE = re.compile(r"(?:^|;\s*)Topic:\s*([^;]+)", re.IGNORECASE)


def normalize_key(name: str) -> str:
    """Comparison key for small naming differences only (never semantic merging)."""
    text = (name or "").casefold().replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = []
    for tok in text.split():
        # "Radicals"/"Radical", "Identities"/"Identity" - plural noise only.
        if len(tok) > 4 and tok.endswith("ies"):
            tok = tok[:-3] + "y"
        elif len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        tokens.append(tok)
    return " ".join(tokens)[:255]


def clean_label(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()[:255]


def normalize_grade(grade: Optional[str]) -> str:
    if not grade:
        return ""
    try:
        from app.services.lms.assessment_service import normalize_grade_level

        return normalize_grade_level(grade) or str(grade).strip()
    except Exception:  # noqa: BLE001
        return str(grade).strip()


def printed_topic_of(question) -> Optional[str]:
    """Printed ``Topic:`` the hybrid extractor keeps in the question explanation."""
    match = _PRINTED_TOPIC_RE.search(str(getattr(question, "explanation", "") or ""))
    if not match:
        return None
    value = clean_label(match.group(1))
    return value[:120] if value else None


# ---------------------------------------------------------------------------
# Subtopic resolution
# ---------------------------------------------------------------------------


def _aliases(sub: Subtopic) -> List[str]:
    try:
        values = json.loads(sub.aliases_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [v for v in values if isinstance(v, str)]


def find_subtopic(domain_topic_id: int, name: str, grade_level: str = "") -> Optional[Subtopic]:
    """Exact (normalised) or confirmed-alias match inside one Domain + grade."""
    key = normalize_key(name)
    if not key or not domain_topic_id:
        return None
    grade = normalize_grade(grade_level)
    db = get_db()
    rows = (
        db.query(Subtopic)
        .filter(Subtopic.domain_topic_id == domain_topic_id, Subtopic.grade_level == grade)
        .all()
    )
    for row in rows:
        if row.name_key == key and row.status == "active":
            return row
    for row in rows:
        if row.status == "active" and key in _aliases(row):
            return row
    # A retired subtopic whose key matches was merged into another: follow it.
    for row in rows:
        if row.name_key == key and row.status == "retired":
            for target in rows:
                if target.status == "active" and key in _aliases(target):
                    return target
    return None


def get_or_create_subtopic(domain_topic_id: int, name: str, grade_level: str = "") -> Optional[Subtopic]:
    """Resolve a printed subtopic label to its canonical row, registering it once."""
    label = clean_label(name)
    if not label or not domain_topic_id:
        return None
    found = find_subtopic(domain_topic_id, label, grade_level)
    if found:
        return found
    db = get_db()
    row = Subtopic(
        domain_topic_id=domain_topic_id,
        grade_level=normalize_grade(grade_level),
        canonical_name=label,
        name_key=normalize_key(label),
        aliases_json="[]",
        status="active",
    )
    db.add(row)
    try:
        db.flush()
    except Exception:  # noqa: BLE001 - concurrent insert of the same key
        db.rollback()
        return find_subtopic(domain_topic_id, label, grade_level)
    return row


def resolve_domain(printed_domain: str) -> Optional[Topic]:
    from app.services.lms.topic_resolver import get_or_create_topic_from_pdf_label
    from app.services.quiz.concept_labeler import is_generic_concept

    label = clean_label(printed_domain)
    if not label or is_generic_concept(label):
        return None
    return get_or_create_topic_from_pdf_label(label)


# ---------------------------------------------------------------------------
# Question classification
# ---------------------------------------------------------------------------


def _assessment_meta(assessment) -> dict:
    if not assessment or not assessment.description:
        return {}
    try:
        meta = json.loads(assessment.description)
    except (json.JSONDecodeError, TypeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _legacy_topic_domain(question: Question, assessment) -> tuple:
    """Papers imported before Domain became the concept stored "Topic / Domain"
    per question - both halves are the labels printed on the paper."""
    stored = (_assessment_meta(assessment).get("question_concepts") or {}).get(str(question.id))
    if stored and " / " in str(stored):
        topic, domain = str(stored).rsplit(" / ", 1)
        return clean_label(topic) or None, clean_label(domain) or None
    return None, None


def _printed_domain_of(question: Question, assessment) -> Optional[str]:
    from app.services.lms.weakness_analyzer import _concepts_are_pdf_domains, _domain_of_stored_label

    meta = _assessment_meta(assessment)
    stored = (meta.get("question_concepts") or {}).get(str(question.id))
    if stored and _concepts_are_pdf_domains(meta):
        return clean_label(_domain_of_stored_label(stored)) or None
    return None


def classify_question(
    question: Question,
    assessment=None,
    *,
    domain_topic_id: Optional[int] = None,
    printed_domain: Optional[str] = None,
    printed_topic: Optional[str] = None,
    refresh: bool = False,
    grade_hint: Optional[str] = None,
) -> QuestionTaxonomy:
    """Canonical Domain/Subtopic for one question (cached in lms_question_taxonomy).

    ``domain_topic_id`` lets a caller pin the Domain to the id its Domain score
    uses (the diagnostic AI grouping), so Subtopic and Domain mastery never
    disagree about which Domain a question belongs to.
    """
    db = get_db()
    # Teacher quizzes are created without a grade: fall back to the grade of the
    # student who sat the paper, so a Follow-up maps onto the same grade-scoped
    # Subtopics as the Baseline and the Target bank.
    grade = normalize_grade(getattr(assessment, "grade_level", None) if assessment else None) or normalize_grade(
        grade_hint
    )
    row = db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id == question.id).first()
    if row and not refresh and row.mapping_status != "unresolved" and (
        domain_topic_id is None or row.domain_topic_id == domain_topic_id
    ) and (row.grade_level or "") == grade:
        return row
    legacy_topic, legacy_domain = _legacy_topic_domain(question, assessment) if assessment is not None else (None, None)
    printed_topic = clean_label(printed_topic or printed_topic_of(question) or legacy_topic or "") or None
    printed_domain = clean_label(
        printed_domain or _printed_domain_of(question, assessment) or legacy_domain or ""
    ) or None

    domain_id = domain_topic_id
    note = None
    if not domain_id and printed_domain:
        topic = resolve_domain(printed_domain)
        domain_id = topic.id if topic else None
    if not domain_id and assessment is not None:
        try:
            from app.services.lms.performance_service import _resolve_question_topic_id

            domain_id = _resolve_question_topic_id(question, assessment)
        except Exception as exc:  # noqa: BLE001
            note = f"domain resolution failed: {exc}"
    if not domain_id and question.topic_id:
        domain_id = question.topic_id

    subtopic_id = None
    if not domain_id:
        status = "unresolved"
        note = note or "no Domain could be determined"
    elif not printed_topic:
        status = "domain_only"
        note = "no printed Topic on the source paper - not tracked at Subtopic level"
    else:
        sub = get_or_create_subtopic(domain_id, printed_topic, grade)
        subtopic_id = sub.id if sub else None
        status = "resolved" if subtopic_id else "domain_only"

    if row is None:
        row = QuestionTaxonomy(question_id=question.id)
        db.add(row)
    row.domain_topic_id = domain_id
    row.subtopic_id = subtopic_id
    row.printed_domain = printed_domain
    row.printed_topic = printed_topic
    row.grade_level = grade or None
    row.mapping_status = status
    row.mapping_note = note
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Lookups and admin operations
# ---------------------------------------------------------------------------


def subtopic_names(ids: Iterable[int]) -> Dict[int, dict]:
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    db = get_db()
    out: Dict[int, dict] = {}
    for sub, domain_name in (
        db.query(Subtopic, Topic.name)
        .join(Topic, Topic.id == Subtopic.domain_topic_id)
        .filter(Subtopic.id.in_(ids))
        .all()
    ):
        out[sub.id] = {
            "subtopic_id": sub.id,
            "subtopic_name": sub.canonical_name,
            "domain_topic_id": sub.domain_topic_id,
            "domain_name": domain_name,
            "grade_level": sub.grade_level,
        }
    return out


def list_taxonomy(grade_level: Optional[str] = None) -> List[dict]:
    """Domain -> Subtopics tree (with question counts) for admin review."""
    from sqlalchemy import func

    db = get_db()
    q = db.query(Subtopic, Topic.name).join(Topic, Topic.id == Subtopic.domain_topic_id)
    if grade_level:
        q = q.filter(Subtopic.grade_level == normalize_grade(grade_level))
    counts = dict(
        db.query(QuestionTaxonomy.subtopic_id, func.count(QuestionTaxonomy.question_id))
        .filter(QuestionTaxonomy.subtopic_id.isnot(None))
        .group_by(QuestionTaxonomy.subtopic_id)
        .all()
    )
    domains: Dict[int, dict] = {}
    for sub, domain_name in q.order_by(Topic.name, Subtopic.canonical_name).all():
        d = domains.setdefault(
            sub.domain_topic_id,
            {"domain_topic_id": sub.domain_topic_id, "domain_name": domain_name, "subtopics": []},
        )
        d["subtopics"].append(
            {
                "subtopic_id": sub.id,
                "name": sub.canonical_name,
                "grade_level": sub.grade_level,
                "status": sub.status,
                "aliases": _aliases(sub),
                "question_count": int(counts.get(sub.id, 0)),
            }
        )
    return list(domains.values())


def merge_subtopic(source_id: int, target_id: int) -> dict:
    """Admin-confirmed alias: ``source`` is the same subtopic as ``target``.

    Re-points questions and evidence, retires ``source`` (its key becomes an alias
    of ``target``) and rebuilds the affected students' subtopic mastery.
    """
    if int(source_id) == int(target_id):
        raise LMSValidationError("A subtopic cannot be merged into itself")
    db = get_db()
    source = db.query(Subtopic).filter(Subtopic.id == source_id).first()
    target = db.query(Subtopic).filter(Subtopic.id == target_id).first()
    if not source or not target:
        raise LMSNotFoundError("Subtopic not found")
    if source.grade_level != target.grade_level:
        raise LMSValidationError("Subtopics from different grades cannot be merged")
    if target.status != "active":
        raise LMSValidationError("Merge into an active subtopic")

    aliases = set(_aliases(target)) | set(_aliases(source)) | {source.name_key}
    target.aliases_json = json.dumps(sorted(aliases))
    source.status = "retired"
    source.aliases_json = "[]"

    db.query(QuestionTaxonomy).filter(QuestionTaxonomy.subtopic_id == source.id).update(
        {QuestionTaxonomy.subtopic_id: target.id, QuestionTaxonomy.domain_topic_id: target.domain_topic_id},
        synchronize_session=False,
    )
    students = {
        sid
        for (sid,) in db.query(SubtopicEvidence.student_id)
        .filter(SubtopicEvidence.subtopic_id == source.id)
        .distinct()
        .all()
    }
    db.query(SubtopicEvidence).filter(SubtopicEvidence.subtopic_id == source.id).update(
        {SubtopicEvidence.subtopic_id: target.id, SubtopicEvidence.domain_topic_id: target.domain_topic_id},
        synchronize_session=False,
    )
    db.query(StudentSubtopicScore).filter(StudentSubtopicScore.subtopic_id == source.id).delete(
        synchronize_session=False
    )
    db.commit()

    from app.services.lms import subtopic_mastery_service

    for sid in students:
        subtopic_mastery_service.recompute_scores(sid, [target.id])
    return {"merged": source.id, "into": target.id, "students_rebuilt": len(students)}
