"""Guided Practice from the approved Target PDF question bank - never AI-generated.

Selection rules:
* only ``approved`` bank items (an admin verified them against the source page);
* only the student's grade;
* focus on Subtopics that are weak for the student *right now* (latest Baseline /
  Follow-up / Learning Chat), optionally narrowed to one Domain or Subtopic;
* when a weak Subtopic has fewer than ``MIN_GUIDED_POOL`` approved items, widen
  the pool to other approved Target-bank questions in the same Domain so the
  student still gets a rotating set of at least ~10 practice items;
* unseen questions first (shuffled), then reshuffle/reuse the least recently
  seen when the finite bank is exhausted - never invent new questions.
* four-parameter matching inside the pool: each question the student got wrong
  (latest Baseline/Follow-up on that Subtopic + Guided Practice misses since)
  is a *target*, and candidates are ranked against it:

    P1 Domain + Topic + Cognitive level + Difficulty
    P2 Domain + Topic + Cognitive level
    P3 Domain + Topic + Difficulty
    P4 Domain + Topic

  A difficulty counts only when both papers printed one (see
  ``question_attributes.known_difficulty``). Wrong answers take turns
  (round-robin) and each is served its own best available match.

A weak Subtopic with no approved questions *and* no Domain bank fill is reported
as a coverage gap (``coverage_gaps``) for admins/teachers.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy import func

from app.models.lms_models import (
    GuidedPracticeAttempt,
    IncorrectAnswerRecord,
    Question,
    QuestionTaxonomy,
    SubtopicEvidence,
    TargetBankDocument,
    TargetBankItem,
)
from app.services.lms import question_attributes as qa
from app.services.lms import subtopic_mastery_service, taxonomy_service
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.mcq_utils import options_from_json
from app.utils.db import get_db

logger = logging.getLogger(__name__)

_LABELS = "ABCD"

# Minimum rotating Target-bank questions Guided Practice should offer per session.
# When the exact weak Subtopic has fewer approved items, same-Domain bank items fill.
MIN_GUIDED_POOL = 10


def _approved_query(db, grade: Optional[str]):
    q = (
        db.query(TargetBankItem, Question, QuestionTaxonomy)
        .join(Question, Question.id == TargetBankItem.question_id)
        .join(QuestionTaxonomy, QuestionTaxonomy.question_id == Question.id)
        .join(TargetBankDocument, TargetBankDocument.id == TargetBankItem.document_id)
        .filter(
            TargetBankItem.verification_status == "approved",
            Question.is_active.is_(True),
            QuestionTaxonomy.subtopic_id.isnot(None),
        )
    )
    if grade:
        q = q.filter(TargetBankDocument.grade_level == grade)
    return q


def approved_counts_for_subtopics(subtopic_ids: Iterable[int], grade: Optional[str] = None) -> Dict[int, int]:
    ids = [int(i) for i in subtopic_ids if i]
    if not ids:
        return {}
    db = get_db()
    q = (
        db.query(QuestionTaxonomy.subtopic_id, func.count(TargetBankItem.id))
        .join(TargetBankItem, TargetBankItem.question_id == QuestionTaxonomy.question_id)
        .join(Question, Question.id == QuestionTaxonomy.question_id)
        .join(TargetBankDocument, TargetBankDocument.id == TargetBankItem.document_id)
        .filter(
            QuestionTaxonomy.subtopic_id.in_(ids),
            TargetBankItem.verification_status == "approved",
            Question.is_active.is_(True),
        )
    )
    if grade:
        q = q.filter(TargetBankDocument.grade_level == grade)
    return {int(k): int(v) for k, v in q.group_by(QuestionTaxonomy.subtopic_id).all()}


def approved_counts_for_domains(domain_topic_ids: Iterable[int], grade: Optional[str] = None) -> Dict[int, int]:
    ids = [int(i) for i in domain_topic_ids if i]
    if not ids:
        return {}
    db = get_db()
    q = (
        db.query(QuestionTaxonomy.domain_topic_id, func.count(TargetBankItem.id))
        .join(TargetBankItem, TargetBankItem.question_id == QuestionTaxonomy.question_id)
        .join(Question, Question.id == QuestionTaxonomy.question_id)
        .join(TargetBankDocument, TargetBankDocument.id == TargetBankItem.document_id)
        .filter(
            QuestionTaxonomy.domain_topic_id.in_(ids),
            TargetBankItem.verification_status == "approved",
            Question.is_active.is_(True),
        )
    )
    if grade:
        q = q.filter(TargetBankDocument.grade_level == grade)
    return {int(k): int(v) for k, v in q.group_by(QuestionTaxonomy.domain_topic_id).all()}


def _practice_available_count(exact: int, domain_total: int) -> int:
    """UI/pool size: exact Subtopic count, or Domain fill when below the minimum."""
    if exact <= 0 and domain_total <= 0:
        return 0
    if exact >= MIN_GUIDED_POOL:
        return exact
    return max(exact, domain_total)


def _student_grade(student_id: int) -> str:
    try:
        from app.services.lms import class_service

        grade = class_service.get_student_diagnostic_grade(student_id) or class_service.get_student_grade(student_id)
    except Exception:  # noqa: BLE001
        grade = None
    return taxonomy_service.normalize_grade(grade)


def eligibility(student_id: int) -> dict:
    """Which weak Subtopics have approved bank questions (drives the UI + gaps)."""
    grade = _student_grade(student_id)
    weak = subtopic_mastery_service.get_weak_subtopics(student_id)
    counts = approved_counts_for_subtopics([w["subtopic_id"] for w in weak], grade or None)
    domain_counts = approved_counts_for_domains(
        [w["domain_topic_id"] for w in weak if w.get("domain_topic_id")], grade or None
    )
    available, gaps = [], []
    for w in weak:
        exact = counts.get(w["subtopic_id"], 0)
        domain_total = domain_counts.get(int(w["domain_topic_id"] or 0), 0)
        available_n = _practice_available_count(exact, domain_total)
        entry = {
            "domain_topic_id": w["domain_topic_id"],
            "domain_name": w["domain_name"],
            "subtopic_id": w["subtopic_id"],
            "subtopic_name": w["subtopic_name"],
            "score_percent": w["score_percent"],
            "approved_questions": available_n,
            "approved_exact": exact,
            "approved_domain": domain_total,
        }
        (available if available_n else gaps).append(entry)
    return {"grade_level": grade, "available": available, "coverage_gaps": gaps}


PRIORITY_LABELS = {
    1: "Domain + Topic + Cognitive level + Difficulty",
    2: "Domain + Topic + Cognitive level",
    3: "Domain + Topic + Difficulty",
    4: "Domain + Topic",
}


def match_priority(target: Optional[dict], cognitive: Optional[str], difficulty: Optional[str]) -> int:
    """1-4 match of a candidate's (cognitive, difficulty) against a wrong-answer target."""
    if not target:
        return 4
    cog_ok = bool(target.get("cognitive_level") and cognitive and target["cognitive_level"] == cognitive)
    diff_ok = bool(target.get("difficulty") and difficulty and target["difficulty"] == difficulty)
    if cog_ok and diff_ok:
        return 1
    if cog_ok:
        return 2
    if diff_ok:
        return 3
    return 4


def incorrect_targets(student_id: int, subtopic_id: int) -> List[dict]:
    """The student's current wrong answers on one Subtopic, oldest first.

    Current = the latest Baseline/Follow-up attempt that scored this Subtopic
    (it *replaces* the score, so older papers no longer describe the weakness)
    plus Guided Practice misses after it.
    """
    db = get_db()
    latest = (
        db.query(SubtopicEvidence)
        .filter(
            SubtopicEvidence.student_id == student_id,
            SubtopicEvidence.subtopic_id == subtopic_id,
            SubtopicEvidence.source_type.in_(subtopic_mastery_service.ASSESSMENT_SOURCES),
        )
        .order_by(SubtopicEvidence.created_at.desc(), SubtopicEvidence.id.desc())
        .first()
    )
    q = db.query(IncorrectAnswerRecord).filter(
        IncorrectAnswerRecord.student_id == student_id,
        IncorrectAnswerRecord.subtopic_id == subtopic_id,
    )
    rows = q.order_by(IncorrectAnswerRecord.created_at.asc(), IncorrectAnswerRecord.id.asc()).all()
    out = []
    for r in rows:
        if r.source_type == "guided_practice":
            if latest is not None and latest.created_at and r.created_at and r.created_at < latest.created_at:
                continue
        elif latest is None or (r.source_type, r.source_id) != (latest.source_type, latest.source_id):
            continue
        out.append(
            {
                "record_id": r.id,
                "question_id": r.question_id,
                "cognitive_level": r.cognitive_level,
                "difficulty": r.difficulty,
                "source_type": r.source_type,
                "source_id": r.source_id,
                "assessment_id": r.assessment_id,
            }
        )
    return out


def _rotate_targets(db, student_id: int, targets: List[dict]) -> List[dict]:
    """Round-robin over wrong answers: never followed up first, then the one followed
    up longest ago. (Ordering by follow-up *count* let a fresh miss win every turn
    until it caught up - real E2E: the same question 5 times in a row.)"""
    if len(targets) <= 1:
        return targets
    last = dict(
        db.query(GuidedPracticeAttempt.target_question_id, func.max(GuidedPracticeAttempt.id))
        .filter(
            GuidedPracticeAttempt.student_id == student_id,
            GuidedPracticeAttempt.target_question_id.in_([t["question_id"] for t in targets]),
        )
        .group_by(GuidedPracticeAttempt.target_question_id)
        .all()
    )
    return sorted(targets, key=lambda t: (last.get(t["question_id"], 0), targets.index(t)))


def _choose_in_subtopic(cands: list, targets: List[dict], last_seen: dict) -> tuple:
    """Back-compat wrapper: pick inside one Subtopic's candidates."""
    return _choose_in_pool(cands, targets, last_seen, order=None)


def _choose_in_pool(
    cands: list,
    targets: List[dict],
    last_seen: dict,
    order: Optional[Dict[int, int]] = None,
) -> Optional[tuple]:
    """(candidate, target, priority, reused) from a Target-bank pool.

    Unseen items are shuffled each turn. Once every item has been seen, the pool
    reshuffles among the least-recently-seen tier so repeats still fluctuate.
    """
    if not cands:
        return None
    attrs = {c[0].id: (qa.cognitive_level_of(c[1]), qa.known_difficulty(c[1])) for c in cands}
    unseen = [c for c in cands if c[0].id not in last_seen]
    pool, reused = (unseen, False) if unseen else (cands, True)
    target = targets[0] if targets else None
    by_prio: Dict[int, list] = {}
    for c in pool:
        by_prio.setdefault(match_priority(target, *attrs[c[0].id]), []).append(c)
    prio = min(by_prio)
    tier = by_prio[prio]
    if reused:
        # Least recently seen first; shuffle within the same timestamp so repeats vary.
        oldest = min(last_seen[c[0].id] for c in tier)
        oldest_tier = [c for c in tier if last_seen[c[0].id] == oldest]
        if order:
            oldest_tier.sort(key=lambda c: order.get(c[2].subtopic_id, 10**9))
        random.shuffle(oldest_tier)
        cand = oldest_tier[0]
    else:
        if order:
            # Prefer weaker Subtopics, then shuffle so consecutive serves differ.
            best_rank = min(order.get(c[2].subtopic_id, 10**9) for c in tier)
            tier = [c for c in tier if order.get(c[2].subtopic_id, 10**9) == best_rank] or tier
        random.shuffle(tier)
        cand = tier[0]
    return cand, target, prio, reused


def _option_order(question: Question) -> List[int]:
    """Shuffle options unless an option refers to its position ("both A and B")."""
    from app.services.lms.deficiency_chat_service import _has_positional_option

    opts = options_from_json(question.options_json)
    order = list(range(len(opts)))
    if not _has_positional_option(opts):
        random.shuffle(order)
    return order


def next_question(
    student_id: int,
    *,
    domain_topic_id: Optional[int] = None,
    subtopic_id: Optional[int] = None,
) -> dict:
    """Serve the next verified Target PDF question for a currently weak Subtopic."""
    elig = eligibility(student_id)
    pool = elig["available"]
    if subtopic_id:
        pool = [p for p in pool if p["subtopic_id"] == int(subtopic_id)]
        if not pool:
            is_weak = any(g["subtopic_id"] == int(subtopic_id) for g in elig["coverage_gaps"])
            return {
                "question": None,
                "reason": "no_verified_questions" if is_weak else "not_weak",
                "message": (
                    "There are no verified practice questions for this subtopic yet. "
                    "Your teacher has been notified." if is_weak else
                    "This subtopic is not one of your current weak areas."
                ),
                "coverage_gaps": elig["coverage_gaps"],
            }
    elif domain_topic_id:
        pool = [p for p in pool if p["domain_topic_id"] == int(domain_topic_id)]
    if not pool:
        return {
            "question": None,
            "reason": "no_weak_subtopics" if not elig["coverage_gaps"] else "no_verified_questions",
            "message": (
                "You have no weak subtopics right now - great work!" if not elig["coverage_gaps"] else
                "Your weak subtopics have no verified practice questions yet. Your teacher has been notified."
            ),
            "coverage_gaps": elig["coverage_gaps"],
        }

    db = get_db()
    # Resume an unanswered question instead of piling up new ones.
    pending = (
        db.query(GuidedPracticeAttempt)
        .filter(GuidedPracticeAttempt.student_id == student_id, GuidedPracticeAttempt.status == "served")
        .order_by(GuidedPracticeAttempt.id.desc())
        .first()
    )
    allowed_subs = {p["subtopic_id"] for p in pool}
    if pending and pending.subtopic_id in allowed_subs:
        item = db.query(TargetBankItem).filter(TargetBankItem.id == pending.bank_item_id).first()
        if item and item.verification_status == "approved":
            # Resume only when the student still has this unanswered item in front of
            # them; if a widened Domain pool has other unseen questions, move on so
            # Guided Practice is not stuck forever on the first (often only exact) hit.
            grade = elig["grade_level"] or None
            weak_sub_ids = {int(p["subtopic_id"]) for p in pool}
            domain_ids = {int(p["domain_topic_id"]) for p in pool if p.get("domain_topic_id")}
            pool_rows = (
                _approved_query(db, grade)
                .filter(QuestionTaxonomy.subtopic_id.in_(list(weak_sub_ids)))
                .all()
            )
            if len(pool_rows) < MIN_GUIDED_POOL and domain_ids:
                wider = (
                    _approved_query(db, grade)
                    .filter(QuestionTaxonomy.domain_topic_id.in_(list(domain_ids)))
                    .all()
                )
                seen_ids = {r[0].id for r in pool_rows}
                pool_rows = list(pool_rows) + [r for r in wider if r[0].id not in seen_ids]
            seen_ids = {
                bid
                for (bid,) in db.query(GuidedPracticeAttempt.bank_item_id)
                .filter(
                    GuidedPracticeAttempt.student_id == student_id,
                    GuidedPracticeAttempt.bank_item_id.in_([r[0].id for r in pool_rows]),
                    GuidedPracticeAttempt.status == "answered",
                )
                .distinct()
                .all()
            }
            unseen_others = [r for r in pool_rows if r[0].id not in seen_ids and r[0].id != item.id]
            if not unseen_others:
                return _serve_payload(
                    pending, item, elig, resumed=True, pool_size=len(pool_rows)
                )
            pending.status = "abandoned"
            db.commit()
    elif pending:
        pending.status = "abandoned"  # its subtopic is no longer weak / no longer targeted
        db.commit()

    # Prefer exact weak Subtopics; if the pool is smaller than MIN_GUIDED_POOL,
    # fill with other approved Target-bank items from the same Domain(s).
    pool.sort(key=lambda p: (p["score_percent"], p["subtopic_name"]))
    grade = elig["grade_level"] or None
    weak_sub_ids: Set[int] = {int(p["subtopic_id"]) for p in pool}
    domain_ids: Set[int] = {int(p["domain_topic_id"]) for p in pool if p.get("domain_topic_id")}
    focus_sub_id = int(subtopic_id) if subtopic_id else int(pool[0]["subtopic_id"])
    order = {p["subtopic_id"]: rank for rank, p in enumerate(pool)}

    exact_candidates = (
        _approved_query(db, grade)
        .filter(QuestionTaxonomy.subtopic_id.in_(list(weak_sub_ids)))
        .all()
    )
    candidates = list(exact_candidates)
    if len(candidates) < MIN_GUIDED_POOL and domain_ids:
        wider = (
            _approved_query(db, grade)
            .filter(QuestionTaxonomy.domain_topic_id.in_(list(domain_ids)))
            .all()
        )
        seen_ids = {c[0].id for c in candidates}
        for row in wider:
            if row[0].id in seen_ids:
                continue
            candidates.append(row)
            seen_ids.add(row[0].id)

    choice = None
    if candidates:
        last_seen = dict(
            db.query(GuidedPracticeAttempt.bank_item_id, func.max(GuidedPracticeAttempt.created_at))
            .filter(
                GuidedPracticeAttempt.student_id == student_id,
                GuidedPracticeAttempt.bank_item_id.in_([i.id for i, _, _ in candidates]),
            )
            .group_by(GuidedPracticeAttempt.bank_item_id)
            .all()
        )
        # Students assessed before wrong answers were recorded: rebuild them once.
        if not db.query(IncorrectAnswerRecord.id).filter(IncorrectAnswerRecord.student_id == student_id).first():
            try:
                subtopic_mastery_service.backfill_incorrect_answers(student_id)
            except Exception as exc:  # noqa: BLE001 - matching degrades to topic-only
                logger.warning("Incorrect-answer backfill failed for student %s: %s", student_id, exc)
                db.rollback()
        targets = _rotate_targets(db, student_id, incorrect_targets(student_id, focus_sub_id))
        # Use the full widened Domain pool. Restricting to exact weak-Subtopic rows
        # left students stuck on a single bank item (e.g. Laws of Logarithms = 1)
        # even when 50+ same-Domain Target questions were available.
        # Weak-Subtopic items still rank first via ``order`` inside _choose_in_pool.
        choice = _choose_in_pool(candidates, targets, last_seen, order)
    if choice:
        (item, question, tax), target, priority, reused = choice
        # Credit mastery to the weak Subtopic the student opened, even when the
        # served bank item is a same-Domain fill used to reach MIN_GUIDED_POOL.
        credit_sub_id = (
            int(tax.subtopic_id)
            if tax.subtopic_id in weak_sub_ids
            else focus_sub_id
        )
        match = {
            "priority": priority,
            "matched_on": PRIORITY_LABELS[priority],
            "target": target,
            "served": {
                "cognitive_level": qa.cognitive_level_of(question),
                "difficulty": qa.known_difficulty(question),
                "printed_difficulty": question.printed_difficulty,  # label as on the paper
                "domain_topic_id": tax.domain_topic_id,
                "subtopic_id": tax.subtopic_id,
                "practice_focus_subtopic_id": credit_sub_id,
            },
            "reason": "matched_incorrect_answer" if target else "no_recorded_incorrect_answer",
        }
        attempt = GuidedPracticeAttempt(
            student_id=student_id,
            bank_item_id=item.id,
            subtopic_id=credit_sub_id,
            option_order_json=json.dumps(_option_order(question)),
            status="served",
            match_priority=priority,
            target_question_id=target["question_id"] if target else None,
            match_json=json.dumps(match),
        )
        db.add(attempt)
        db.commit()
        payload = _serve_payload(attempt, item, elig, resumed=False, pool_size=len(candidates))
        payload["bank_exhausted"] = reused
        if reused:
            payload["message"] = (
                "You have practised every verified question in this set - "
                "questions are shuffled so you can keep practising."
            )
        return payload
    db.commit()
    return {"question": None, "reason": "no_verified_questions", "message": "No verified questions available.",
            "coverage_gaps": elig["coverage_gaps"]}


def _serve_payload(
    attempt: GuidedPracticeAttempt,
    item: TargetBankItem,
    elig: dict,
    *,
    resumed: bool,
    pool_size: Optional[int] = None,
) -> dict:
    from app.services.lms.target_bank_service import bank_render

    db = get_db()
    question = db.query(Question).filter(Question.id == item.question_id).first()
    options = options_from_json(question.options_json)
    order = json.loads(attempt.option_order_json or "[]") or list(range(len(options)))
    info = taxonomy_service.subtopic_names([attempt.subtopic_id]).get(attempt.subtopic_id, {})
    seen = (
        db.query(func.count(func.distinct(GuidedPracticeAttempt.bank_item_id)))
        .filter(
            GuidedPracticeAttempt.student_id == attempt.student_id,
            GuidedPracticeAttempt.subtopic_id == attempt.subtopic_id,
        )
        .scalar()
    )
    total = next(
        (p["approved_questions"] for p in elig["available"] if p["subtopic_id"] == attempt.subtopic_id),
        0,
    )
    if pool_size is not None:
        total = max(int(total or 0), int(pool_size))
    return {
        "attempt_id": attempt.id,
        "resumed": resumed,
        "question": {
            "question_text": question.question_text,
            "question_render": bank_render(question.question_text),
            # Display positions only: the correct answer never leaves the server.
            "options": [
                {
                    "position": pos,
                    "label": _LABELS[pos],
                    "text": options[orig].get("text"),
                    "render": bank_render(options[orig].get("text") or ""),
                }
                for pos, orig in enumerate(order)
            ],
            "source": "target_pdf_bank",
            "source_question_number": item.source_question_number,
        },
        "domain_name": info.get("domain_name"),
        "subtopic_name": info.get("subtopic_name"),
        "subtopic_id": attempt.subtopic_id,
        "progress": {"seen_in_subtopic": int(seen or 0), "approved_in_subtopic": int(total)},
        "coverage_gaps": elig["coverage_gaps"],
        # Why this question (never contains the answer key).
        "match": json.loads(attempt.match_json) if attempt.match_json else None,
    }


def submit_answer(student_id: int, attempt_id: int, position: int, time_spent_seconds=None) -> dict:
    db = get_db()
    attempt = (
        db.query(GuidedPracticeAttempt)
        .filter(GuidedPracticeAttempt.id == attempt_id, GuidedPracticeAttempt.student_id == student_id)
        .first()
    )
    if not attempt:
        raise LMSNotFoundError("Practice question not found")
    if attempt.status != "served":
        raise LMSValidationError("This practice question was already answered")
    item = db.query(TargetBankItem).filter(TargetBankItem.id == attempt.bank_item_id).first()
    question = db.query(Question).filter(Question.id == item.question_id).first()
    order = json.loads(attempt.option_order_json or "[]") or list(range(4))
    if not 0 <= int(position) < len(order):
        raise LMSValidationError("That option does not exist for this question")
    original = order[int(position)]
    attempt.selected_original_index = original
    attempt.is_correct = original == question.correct_option_index
    attempt.status = "answered"
    attempt.answered_at = datetime.utcnow()
    if time_spent_seconds is not None:
        try:
            sec = max(0, min(int(time_spent_seconds), 2 * 60 * 60))
            attempt.time_spent_seconds = max(int(attempt.time_spent_seconds or 0), sec)
        except (TypeError, ValueError):
            pass
    db.commit()
    try:
        subtopic_mastery_service.record_guided_practice(attempt)
    except Exception as exc:  # noqa: BLE001 - evidence is an audit trail only
        logger.warning("Guided practice evidence failed for attempt %s: %s", attempt.id, exc)
        db.rollback()
    correct_position = order.index(question.correct_option_index) if question.correct_option_index in order else None
    return {
        "correct": bool(attempt.is_correct),
        "correct_position": correct_position,
        "correct_label": _LABELS[correct_position] if correct_position is not None else None,
        "time_spent_seconds": attempt.time_spent_seconds,
    }


def history(student_id: int, limit: int = 50) -> List[dict]:
    db = get_db()
    rows = (
        db.query(GuidedPracticeAttempt)
        .filter(GuidedPracticeAttempt.student_id == student_id, GuidedPracticeAttempt.status == "answered")
        .order_by(GuidedPracticeAttempt.id.desc())
        .limit(limit)
        .all()
    )
    names = taxonomy_service.subtopic_names(r.subtopic_id for r in rows if r.subtopic_id)
    return [
        {
            "attempt_id": r.id,
            "subtopic_name": (names.get(r.subtopic_id) or {}).get("subtopic_name"),
            "correct": bool(r.is_correct),
            "answered_at": r.answered_at.isoformat() if r.answered_at else None,
            "time_spent_seconds": r.time_spent_seconds,
        }
        for r in rows
    ]


def coverage_gaps_for_students(student_ids: Iterable[int]) -> List[dict]:
    """Weak Subtopics with no approved bank questions, aggregated (admin/teacher view)."""
    gaps: Dict[int, dict] = {}
    for sid in student_ids:
        for g in eligibility(int(sid))["coverage_gaps"]:
            entry = gaps.setdefault(g["subtopic_id"], {**g, "students_affected": 0})
            entry.pop("score_percent", None)
            entry["students_affected"] += 1
    return sorted(gaps.values(), key=lambda g: -g["students_affected"])
