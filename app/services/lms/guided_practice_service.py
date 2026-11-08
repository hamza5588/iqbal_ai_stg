"""Guided Practice from the approved Target PDF question bank - never AI-generated.

Selection rules:
* only ``approved`` bank items (an admin verified them against the source page);
* only the student's grade;
* only Subtopics that are weak for the student *right now* (latest Baseline /
  Follow-up / Learning Chat), optionally narrowed to one Domain or Subtopic -
  a broad Domain match alone never qualifies a question;
* unseen questions first, then the least recently seen (the bank is finite, so
  questions are reused - and the response says so - rather than invented).
* four-parameter matching inside the chosen Subtopic: each question the student
  got wrong (latest Baseline/Follow-up on that Subtopic + Guided Practice misses
  since) is a *target*, and candidates are ranked against it:

    P1 Domain + Topic + Cognitive level + Difficulty
    P2 Domain + Topic + Cognitive level
    P3 Domain + Topic + Difficulty
    P4 Domain + Topic

  A difficulty counts only when both papers printed one (see
  ``question_attributes.known_difficulty``). Wrong answers take turns
  (round-robin) and each is served its own best available match; an unseen
  question always beats a repeat, and once a bank is exhausted the repeats
  rotate across the weak Subtopics (least recently practised first).

A weak Subtopic with no approved questions is reported as a coverage gap
(``coverage_gaps``) for admins/teachers; nothing is substituted for it.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from typing import Dict, Iterable, List, Optional

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
    available, gaps = [], []
    for w in weak:
        entry = {
            "domain_topic_id": w["domain_topic_id"],
            "domain_name": w["domain_name"],
            "subtopic_id": w["subtopic_id"],
            "subtopic_name": w["subtopic_name"],
            "score_percent": w["score_percent"],
            "approved_questions": counts.get(w["subtopic_id"], 0),
        }
        (available if entry["approved_questions"] else gaps).append(entry)
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
    """(candidate, target, priority, reused) for one Subtopic's approved candidates."""
    attrs = {c[0].id: (qa.cognitive_level_of(c[1]), qa.known_difficulty(c[1])) for c in cands}
    unseen = [c for c in cands if c[0].id not in last_seen]
    pool, reused = (unseen, False) if unseen else (cands, True)
    # Each wrong answer gets its turn (``targets`` is already rotated, least
    # followed-up first) and is served its own best available match. Choosing the
    # best (priority, target) pair over all targets instead starved any wrong answer
    # the bank can only match at P2-P4 (real E2E: an Analysis/Reasoning miss).
    target = targets[0] if targets else None
    by_prio: Dict[int, list] = {}
    for c in pool:
        by_prio.setdefault(match_priority(target, *attrs[c[0].id]), []).append(c)
    prio = min(by_prio)
    tier = by_prio[prio]
    if reused:
        cand = min(tier, key=lambda c: last_seen[c[0].id])
    else:
        cand = random.choice(tier)
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
            return _serve_payload(pending, item, elig, resumed=True)
    if pending:
        pending.status = "abandoned"  # its subtopic is no longer weak / no longer targeted

    # Unseen questions first across ALL targeted weak subtopics (weakest subtopic
    # first); only when every one is exhausted, repeat the least recently seen.
    pool.sort(key=lambda p: (p["score_percent"], p["subtopic_name"]))
    grade = elig["grade_level"] or None
    order = {p["subtopic_id"]: rank for rank, p in enumerate(pool)}
    candidates = (
        _approved_query(db, grade)
        .filter(QuestionTaxonomy.subtopic_id.in_(list(order)))
        .all()
    )
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
        # Which weak Subtopic: weakest one that still has unseen questions, else
        # the one practised least recently (by its *latest* serve). Keying on the
        # oldest single question starved Subtopics: repeats stay in the best-match
        # tier, so a never-repeated low tier kept its Subtopic "oldest" forever
        # (real E2E: 30/30 repeats in one Subtopic, Factor Theorem never again).
        unseen = [c for c in candidates if c[0].id not in last_seen]
        if unseen:
            sub_id = min(unseen, key=lambda c: order[c[2].subtopic_id])[2].subtopic_id
        else:
            latest: Dict[int, datetime] = {}
            for c in candidates:
                sid = c[2].subtopic_id
                latest[sid] = max(latest.get(sid, last_seen[c[0].id]), last_seen[c[0].id])
            sub_id = min(latest, key=lambda sid: (latest[sid], order[sid]))
        # Students assessed before wrong answers were recorded: rebuild them once.
        if not db.query(IncorrectAnswerRecord.id).filter(IncorrectAnswerRecord.student_id == student_id).first():
            try:
                subtopic_mastery_service.backfill_incorrect_answers(student_id)
            except Exception as exc:  # noqa: BLE001 - matching degrades to topic-only
                logger.warning("Incorrect-answer backfill failed for student %s: %s", student_id, exc)
                db.rollback()
        targets = _rotate_targets(db, student_id, incorrect_targets(student_id, sub_id))
        sub_cands = [c for c in candidates if c[2].subtopic_id == sub_id]
        choice = _choose_in_subtopic(sub_cands, targets, last_seen)
    if choice:
        (item, question, tax), target, priority, reused = choice
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
            },
            "reason": "matched_incorrect_answer" if target else "no_recorded_incorrect_answer",
        }
        attempt = GuidedPracticeAttempt(
            student_id=student_id,
            bank_item_id=item.id,
            subtopic_id=tax.subtopic_id,
            option_order_json=json.dumps(_option_order(question)),
            status="served",
            match_priority=priority,
            target_question_id=target["question_id"] if target else None,
            match_json=json.dumps(match),
        )
        db.add(attempt)
        db.commit()
        payload = _serve_payload(attempt, item, elig, resumed=False)
        payload["bank_exhausted"] = reused
        if reused:
            payload["message"] = (
                "You have practised every verified question for your weak subtopics - "
                "here is one again so you can keep practising."
            )
        return payload
    db.commit()
    return {"question": None, "reason": "no_verified_questions", "message": "No verified questions available.",
            "coverage_gaps": elig["coverage_gaps"]}


def _serve_payload(attempt: GuidedPracticeAttempt, item: TargetBankItem, elig: dict, *, resumed: bool) -> dict:
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
    total = next((p["approved_questions"] for p in elig["available"] if p["subtopic_id"] == attempt.subtopic_id), 0)
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


def submit_answer(student_id: int, attempt_id: int, position: int) -> dict:
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
