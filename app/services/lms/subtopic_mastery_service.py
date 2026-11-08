"""Subtopic-level mastery, kept beside (never instead of) Domain mastery.

Calculation policy ``subtopic_v1`` - deliberately the same rules Domain mastery
already uses in ``performance_service._upsert_topic_score``:

* **Baseline / Follow-up assessment** answers on a subtopic *replace* its score
  with that attempt's correct/total. The newest assessment is the source of truth,
  so a Follow-up can clear a weakness or create a new one.
* **Learning Chat** answers *blend*: each correct answer cancels one outstanding
  miss, each wrong answer adds one (sample size capped at 20).
* **Guided Practice** answers are recorded as evidence but do not move mastery
  (Guided Practice never moved Domain mastery either).
* Weak = below ``performance_service.PRACTICE_TARGET_THRESHOLD`` (100%).

Every answer is stored once in ``lms_subtopic_evidence`` (idempotent per
student/source/question), so a score can always be rebuilt and audited by
replaying evidence oldest-first. Historical attempts are never deleted.
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict, defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from app.models.lms_models import (
    AssessmentAttempt,
    AttemptAnswer,
    Question,
    StudentSubtopicScore,
    SubtopicEvidence,
)
from app.services.lms import taxonomy_service
from app.utils.db import get_db

logger = logging.getLogger(__name__)

CALC_VERSION = "subtopic_v1"
ASSESSMENT_SOURCES = ("diagnostic", "quiz")
_MAX_BLEND_SAMPLE = 20


def _threshold() -> float:
    from app.services.lms.performance_service import PRACTICE_TARGET_THRESHOLD

    return float(PRACTICE_TARGET_THRESHOLD)


def apply_event(state: Optional[Tuple[float, int]], source_type: str, correct: int, total: int):
    """One mastery step. ``state`` is (score_percent, sample_size) or None."""
    if total <= 0:
        return state
    pct = 100.0 * correct / total
    if state is None or source_type in ASSESSMENT_SOURCES:
        return (pct, total)
    prev_pct, prev_ss = state
    prev_ss = prev_ss or 1
    prev_wrong = round(prev_ss * (100.0 - prev_pct) / 100.0)
    new_wrong = total - correct
    wrong = max(0, prev_wrong - correct) + new_wrong
    tot = prev_ss + new_wrong
    return (100.0 * (tot - wrong) / tot, min(tot, _MAX_BLEND_SAMPLE))


# ---------------------------------------------------------------------------
# Evidence recording
# ---------------------------------------------------------------------------


def _add_evidence(db, *, student_id, subtopic_id, domain_topic_id, source_type, source_id,
                  question_ref, is_correct, at, counts=True) -> bool:
    exists = (
        db.query(SubtopicEvidence.id)
        .filter(
            SubtopicEvidence.student_id == student_id,
            SubtopicEvidence.source_type == source_type,
            SubtopicEvidence.source_id == source_id,
            SubtopicEvidence.question_ref == str(question_ref),
        )
        .first()
    )
    if exists:
        return False
    db.add(
        SubtopicEvidence(
            student_id=student_id,
            subtopic_id=subtopic_id,
            domain_topic_id=domain_topic_id,
            source_type=source_type,
            source_id=source_id,
            question_ref=str(question_ref),
            is_correct=bool(is_correct),
            counts_toward_mastery=counts,
            created_at=at or datetime.utcnow(),
        )
    )
    return True


def _record_incorrect(db, *, student_id, question, subtopic_id, domain_topic_id, source_type, source_id,
                      selected_index, assessment_id=None, at=None) -> bool:
    """Snapshot one wrong answer with its four matching parameters (idempotent)."""
    from app.models.lms_models import IncorrectAnswerRecord
    from app.services.lms import question_attributes as qa

    if question is None:
        return False
    exists = (
        db.query(IncorrectAnswerRecord.id)
        .filter(
            IncorrectAnswerRecord.student_id == student_id,
            IncorrectAnswerRecord.source_type == source_type,
            IncorrectAnswerRecord.source_id == source_id,
            IncorrectAnswerRecord.question_id == question.id,
        )
        .first()
    )
    if exists:
        return False
    db.add(
        IncorrectAnswerRecord(
            student_id=student_id,
            question_id=question.id,
            domain_topic_id=domain_topic_id,
            subtopic_id=subtopic_id,
            cognitive_level=qa.cognitive_level_of(question),
            difficulty=qa.known_difficulty(question),
            selected_option_index=selected_index,
            correct_option_index=question.correct_option_index,
            source_type=source_type,
            source_id=source_id,
            assessment_id=assessment_id,
            created_at=at or datetime.utcnow(),
        )
    )
    return True


def backfill_incorrect_answers(student_id: int) -> int:
    """Rebuild missing wrong-answer records from assessment evidence (historical attempts)."""
    db = get_db()
    rows = (
        db.query(SubtopicEvidence, AssessmentAttempt)
        .join(AssessmentAttempt, AssessmentAttempt.id == SubtopicEvidence.source_id)
        .filter(
            SubtopicEvidence.student_id == student_id,
            SubtopicEvidence.source_type.in_(ASSESSMENT_SOURCES),
            SubtopicEvidence.is_correct.is_(False),
        )
        .all()
    )
    if not rows:
        return 0
    qids = {int(ev.question_ref) for ev, _ in rows if str(ev.question_ref).isdigit()}
    questions = {q.id: q for q in db.query(Question).filter(Question.id.in_(qids)).all()} if qids else {}
    answers = {
        (a.attempt_id, a.question_id): a
        for a in db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id.in_({att.id for _, att in rows})).all()
    }
    added = 0
    for ev, att in rows:
        if not str(ev.question_ref).isdigit():
            continue
        qid = int(ev.question_ref)
        ans = answers.get((att.id, qid))
        added += _record_incorrect(
            db, student_id=student_id, question=questions.get(qid), subtopic_id=ev.subtopic_id,
            domain_topic_id=ev.domain_topic_id, source_type=ev.source_type, source_id=att.id,
            selected_index=ans.selected_option_index if ans else None,
            assessment_id=att.assessment_id, at=ev.created_at,
        )
    db.commit()
    return added


def _diagnostic_domain_hints(attempt_id: int) -> Dict[int, int]:
    """question_id -> Domain topic id from the same AI grouping Domain scores use."""
    try:
        from app.services.lms.topic_resolver import get_or_create_topic_from_pdf_label
        from app.services.lms.weakness_analyzer import analyze_diagnostic_attempt

        analysis = analyze_diagnostic_attempt(attempt_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("subtopic: diagnostic grouping unavailable for attempt %s: %s", attempt_id, exc)
        return {}
    hints: Dict[int, int] = {}
    for area in analysis.get("all_topics") or []:
        tid = area.get("topic_id")
        name = (area.get("topic_name") or "").strip()
        if name:
            topic = get_or_create_topic_from_pdf_label(name)
            tid = topic.id if topic else tid
        if not tid:
            continue
        for qid in area.get("question_ids") or []:
            hints[int(qid)] = int(tid)
    return hints


def record_attempt(attempt_id: int) -> List[int]:
    """Record a submitted Baseline/Follow-up attempt and refresh subtopic mastery.

    Returns the subtopic ids it touched. Unanswered questions of a timed-out
    attempt are skipped (never reached), mirroring Domain scoring.
    """
    from app.services.lms.assessment_service import get_assessment
    from app.services.lms.attempt_service import attempt_question_ids

    db = get_db()
    attempt = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id).first()
    if not attempt or attempt.status != "submitted":
        return []
    assessment = get_assessment(attempt.assessment_id)
    source_type = "diagnostic" if assessment.assessment_type == "diagnostic" else "quiz"
    timed_out = bool(getattr(attempt, "timed_out", False))
    student_grade = None
    if not getattr(assessment, "grade_level", None):
        try:
            from app.services.lms import class_service

            student_grade = class_service.get_student_grade(attempt.student_id)
        except Exception:  # noqa: BLE001
            student_grade = None
    hints = _diagnostic_domain_hints(attempt.id) if source_type == "diagnostic" and not timed_out else {}

    qids = attempt_question_ids(attempt)
    questions = {q.id: q for q in db.query(Question).filter(Question.id.in_(qids)).all()}
    answers = {
        a.question_id: a
        for a in db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt.id).all()
    }
    touched: set[int] = set()
    for qid in qids:
        q = questions.get(qid)
        if q is None:
            continue
        ans = answers.get(qid)
        if timed_out and (ans is None or ans.selected_option_index is None):
            continue
        tax = taxonomy_service.classify_question(
            q, assessment, domain_topic_id=hints.get(qid), grade_hint=student_grade
        )
        if not tax.subtopic_id:
            continue
        _add_evidence(
            db,
            student_id=attempt.student_id,
            subtopic_id=tax.subtopic_id,
            domain_topic_id=tax.domain_topic_id,
            source_type=source_type,
            source_id=attempt.id,
            question_ref=qid,
            is_correct=bool(ans and ans.is_correct),
            at=attempt.submitted_at,
        )
        if not (ans and ans.is_correct):
            _record_incorrect(
                db, student_id=attempt.student_id, question=q, subtopic_id=tax.subtopic_id,
                domain_topic_id=tax.domain_topic_id, source_type=source_type, source_id=attempt.id,
                selected_index=ans.selected_option_index if ans else None,
                assessment_id=attempt.assessment_id, at=attempt.submitted_at,
            )
        touched.add(tax.subtopic_id)
    db.commit()
    if touched:
        recompute_scores(attempt.student_id, touched)
    return sorted(touched)


def record_learning_chat_session(session_id: int) -> List[int]:
    """Record a completed Learning Chat session's answers on their subtopics."""
    from app.models.lms_models import DeficiencyChatSession

    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session:
        return []
    try:
        items = json.loads(session.questions_json or "[]")
    except json.JSONDecodeError:
        return []
    touched: set[int] = set()
    for index, item in enumerate(items):
        sub_id = item.get("subtopic_id")
        if not item.get("answered") or not sub_id:
            continue
        _add_evidence(
            db,
            student_id=session.student_id,
            subtopic_id=int(sub_id),
            domain_topic_id=item.get("topic_id") or None,
            source_type="learning_chat",
            source_id=session.id,
            question_ref=index,
            is_correct=item.get("correct") is True,
            at=session.updated_at,
        )
        touched.add(int(sub_id))
    db.commit()
    if touched:
        recompute_scores(session.student_id, touched)
    return sorted(touched)


def record_guided_practice(attempt) -> None:
    """Evidence only: Guided Practice does not change mastery (existing policy)."""
    if not attempt.subtopic_id:
        return
    db = get_db()
    _add_evidence(
        db,
        student_id=attempt.student_id,
        subtopic_id=attempt.subtopic_id,
        domain_topic_id=None,
        source_type="guided_practice",
        source_id=attempt.id,
        question_ref=attempt.bank_item_id,
        is_correct=bool(attempt.is_correct),
        at=attempt.answered_at,
        counts=False,
    )
    if not attempt.is_correct:
        from app.models.lms_models import QuestionTaxonomy, TargetBankItem

        item = db.query(TargetBankItem).filter(TargetBankItem.id == attempt.bank_item_id).first()
        question = db.query(Question).filter(Question.id == item.question_id).first() if item else None
        tax = (
            db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id == question.id).first()
            if question else None
        )
        _record_incorrect(
            db, student_id=attempt.student_id, question=question, subtopic_id=attempt.subtopic_id,
            domain_topic_id=tax.domain_topic_id if tax else None, source_type="guided_practice",
            source_id=attempt.id, selected_index=attempt.selected_original_index, at=attempt.answered_at,
        )
    db.commit()


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def recompute_scores(student_id: int, subtopic_ids: Optional[Iterable[int]] = None) -> None:
    """Rebuild StudentSubtopicScore rows from evidence (oldest first)."""
    from app.services.lms.performance_service import compute_mastery_status

    db = get_db()
    q = db.query(SubtopicEvidence).filter(
        SubtopicEvidence.student_id == student_id,
        SubtopicEvidence.counts_toward_mastery.is_(True),
    )
    ids = sorted({int(i) for i in subtopic_ids}) if subtopic_ids is not None else None
    if ids is not None:
        if not ids:
            return
        q = q.filter(SubtopicEvidence.subtopic_id.in_(ids))
    rows = q.order_by(SubtopicEvidence.created_at.asc(), SubtopicEvidence.id.asc()).all()

    # subtopic -> ordered events {(source_type, source_id): [correct, total, at, domain]}
    events: Dict[int, "OrderedDict[tuple, list]"] = defaultdict(OrderedDict)
    for ev in rows:
        bucket = events[ev.subtopic_id].setdefault(
            (ev.source_type, ev.source_id), [0, 0, ev.created_at, ev.domain_topic_id]
        )
        bucket[1] += 1
        bucket[0] += 1 if ev.is_correct else 0
        bucket[2] = max(bucket[2], ev.created_at) if bucket[2] and ev.created_at else (ev.created_at or bucket[2])
        bucket[3] = ev.domain_topic_id or bucket[3]

    existing = {
        r.subtopic_id: r
        for r in db.query(StudentSubtopicScore).filter(StudentSubtopicScore.student_id == student_id).all()
        if ids is None or r.subtopic_id in ids
    }
    for sub_id, evs in events.items():
        state = None
        prev_pct = None
        baseline = None
        latest_assess = None
        last_source = None
        last_at = None
        domain_id = None
        correct = total = 0
        for (source_type, _sid), (c, t, at, dom) in evs.items():
            prev_pct = state[0] if state else None
            state = apply_event(state, source_type, c, t)
            if source_type == "diagnostic" and baseline is None:
                baseline = 100.0 * c / t
            if source_type in ASSESSMENT_SOURCES:
                latest_assess = 100.0 * c / t
                correct, total = c, t
            else:
                correct, total = correct + c, total + t
            last_source, last_at, domain_id = source_type, at, dom or domain_id
        if state is None:
            continue
        row = existing.pop(sub_id, None)
        if row is None:
            row = StudentSubtopicScore(student_id=student_id, subtopic_id=sub_id)
            db.add(row)
        row.domain_topic_id = domain_id
        row.score_percent = round(state[0], 2)
        row.sample_size = state[1]
        row.correct_count = correct
        row.total_count = total
        row.mastery_status = compute_mastery_status(state[0], prev_pct)
        row.baseline_percent = None if baseline is None else round(baseline, 2)
        row.latest_assessment_percent = None if latest_assess is None else round(latest_assess, 2)
        row.last_source = last_source
        row.last_assessed_at = last_at
        row.calc_version = CALC_VERSION
    # Subtopics whose evidence vanished (e.g. merged away) lose their row.
    for row in existing.values():
        if row.subtopic_id not in events:
            db.delete(row)
    db.commit()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def get_subtopic_mastery(student_id: int, domain_topic_id: Optional[int] = None) -> List[dict]:
    db = get_db()
    q = db.query(StudentSubtopicScore).filter(StudentSubtopicScore.student_id == student_id)
    if domain_topic_id:
        q = q.filter(StudentSubtopicScore.domain_topic_id == domain_topic_id)
    rows = q.all()
    names = taxonomy_service.subtopic_names(r.subtopic_id for r in rows)
    threshold = _threshold()
    out = []
    for r in rows:
        info = names.get(r.subtopic_id) or {}
        out.append(
            {
                "subtopic_id": r.subtopic_id,
                "subtopic_name": info.get("subtopic_name") or f"Subtopic #{r.subtopic_id}",
                "domain_topic_id": r.domain_topic_id or info.get("domain_topic_id"),
                "domain_name": info.get("domain_name"),
                "score_percent": r.score_percent,
                "sample_size": r.sample_size,
                "correct_count": r.correct_count,
                "total_count": r.total_count,
                "mastery_status": r.mastery_status,
                "baseline_percent": r.baseline_percent,
                "latest_assessment_percent": r.latest_assessment_percent,
                "last_source": r.last_source,
                "last_assessed_at": r.last_assessed_at.isoformat() if r.last_assessed_at else None,
                "is_weak": (r.score_percent or 0) < threshold,
                "calc_version": r.calc_version,
            }
        )
    out.sort(key=lambda x: (x["domain_name"] or "", x["score_percent"], x["subtopic_name"]))
    return out


def get_weak_subtopics(student_id: int, domain_topic_id: Optional[int] = None) -> List[dict]:
    weak = [r for r in get_subtopic_mastery(student_id, domain_topic_id) if r["is_weak"]]
    weak.sort(key=lambda r: (r["score_percent"], r["subtopic_name"]))
    return weak


def domains_with_subtopic_data(student_id: int) -> set:
    db = get_db()
    return {
        d
        for (d,) in db.query(StudentSubtopicScore.domain_topic_id)
        .filter(StudentSubtopicScore.student_id == student_id)
        .distinct()
        .all()
        if d
    }


def weakness_signature(student_id: int) -> str:
    """Stable fingerprint of the current weak Domains + Subtopics (path/version checks)."""
    from app.services.lms.performance_service import get_weak_topics_for_student

    domains = sorted(int(w.get("topic_id") or 0) for w in get_weak_topics_for_student(student_id))
    subs = sorted(r["subtopic_id"] for r in get_weak_subtopics(student_id))
    return json.dumps({"d": domains, "s": subs}, separators=(",", ":"))
