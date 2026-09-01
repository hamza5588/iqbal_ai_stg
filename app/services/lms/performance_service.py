"""Student performance and mastery tracking."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from app.models.lms_models import AssessmentAttempt, AttemptAnswer, Assessment, MasterySnapshot, Question, StudentTopicScore
from app.services.lms.assessment_service import get_assessment
from app.services.lms.exceptions import LMSNotFoundError
from app.services.lms.topic_resolver import (
    get_or_create_topic_from_pdf_label,
    resolve_topic_id_from_label,
    resolve_topic_id_from_text,
)
from app.utils.db import get_db

logger = logging.getLogger(__name__)

MASTERED_THRESHOLD = 85.0
WEAK_THRESHOLD = 60.0


def _parse_assessment_meta(assessment) -> dict:
    if not assessment or not assessment.description:
        return {}
    try:
        meta = json.loads(assessment.description)
    except (json.JSONDecodeError, TypeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _question_pdf_label(question: Question, assessment) -> Optional[str]:
    meta = _parse_assessment_meta(assessment)
    pdf_map = meta.get("question_pdf_topics") or {}
    label = pdf_map.get(str(question.id))
    if label and str(label).strip():
        return str(label).strip()
    return None


def repair_diagnostic_topic_meta(assessment) -> bool:
    """Backfill PDF section labels on older diagnostics that defaulted to Algebra."""
    if not assessment or assessment.assessment_type != "diagnostic":
        return False

    meta = _parse_assessment_meta(assessment)
    if meta.get("question_pdf_topics"):
        return False

    source_topics = meta.get("source_topics") or []
    # Do not infer topics from every PDF heading — only teacher-selected source_topics.
    if not source_topics:
        return False

    q_ids = [aq.question_id for aq in assessment.questions]
    if not q_ids:
        return False

    db = get_db()
    questions = db.query(Question).filter(Question.id.in_(q_ids)).all()
    q_by_id = {q.id: q for q in questions}
    mapping: dict[str, str] = {}

    if len(source_topics) == 1:
        name = (source_topics[0].get("name") or source_topics[0].get("pdf_label") or "").strip()
        if name:
            mapping = {str(qid): name for qid in q_ids}
    else:
        idx = 0
        for entry in source_topics:
            name = (entry.get("name") or entry.get("pdf_label") or "").strip()
            if not name:
                continue
            count = int(entry.get("question_count") or 1)
            for _ in range(max(1, count)):
                if idx >= len(q_ids):
                    break
                mapping[str(q_ids[idx])] = name
                idx += 1

    if not mapping:
        return False

    meta["question_pdf_topics"] = mapping
    assessment.description = json.dumps(meta, ensure_ascii=False)

    for qid, label in mapping.items():
        topic = get_or_create_topic_from_pdf_label(label)
        if not topic:
            continue
        q = q_by_id.get(int(qid))
        if q:
            q.topic_id = topic.id

    db.commit()
    return True


def _resolve_question_topic_id(question: Question, assessment) -> Optional[int]:
    pdf_label = _question_pdf_label(question, assessment)
    if pdf_label:
        topic = get_or_create_topic_from_pdf_label(pdf_label)
        if topic:
            return topic.id

    if question.topic_id:
        try:
            from app.services.lms import curriculum_service

            topic = curriculum_service.get_topic_by_id(question.topic_id)
            if topic.subject != "Math" or assessment.creation_mode != "pdf_ai":
                return question.topic_id
        except Exception:
            return question.topic_id

    tid = resolve_topic_id_from_text(question.question_text or "")
    if tid:
        return tid

    if question.source_pdf_thread_id:
        from app.models.database_models import RAGHeading

        db = get_db()
        headings = (
            db.query(RAGHeading.heading)
            .filter(RAGHeading.thread_id == question.source_pdf_thread_id)
            .limit(20)
            .all()
        )
        for (heading,) in headings:
            topic = get_or_create_topic_from_pdf_label(heading)
            if topic:
                return topic.id
            tid = resolve_topic_id_from_label(heading)
            if tid:
                return tid

    meta = _parse_assessment_meta(assessment)
    for entry in meta.get("source_topics") or []:
        name = (entry.get("name") or entry.get("pdf_label") or "").strip()
        if name:
            topic = get_or_create_topic_from_pdf_label(name)
            if topic:
                return topic.id
        tid = entry.get("topic_id")
        if tid is not None and assessment.creation_mode != "pdf_ai":
            return int(tid)

    tid = resolve_topic_id_from_label(assessment.title if assessment else "")
    if tid:
        return tid

    return None


def _topic_display_name(topic_id: int, fallback: str = "Topic") -> str:
    try:
        from app.services.lms import curriculum_service

        return curriculum_service.get_topic_by_id(topic_id).name
    except Exception:
        return fallback


def compute_mastery_status(score_percent: float, previous: Optional[float] = None) -> str:
    if score_percent >= MASTERED_THRESHOLD:
        return "mastered"
    if score_percent < WEAK_THRESHOLD:
        return "weak"
    if previous is not None and score_percent > previous:
        return "improving"
    return "needs_practice"


def update_topic_scores_from_attempt(attempt_id: int) -> None:
    db = get_db()
    attempt = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id).first()
    if not attempt or attempt.status != "submitted":
        return

    assessment = get_assessment(attempt.assessment_id)
    if assessment.assessment_type == "diagnostic":
        repair_diagnostic_topic_meta(assessment)
        assessment = get_assessment(attempt.assessment_id)

    q_ids = [aq.question_id for aq in assessment.questions]
    questions = db.query(Question).filter(Question.id.in_(q_ids)).all()
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt_id).all()
    ans_map = {a.question_id: a for a in answers}

    by_topic: dict[int, dict] = {}
    for q in questions:
        topic_id = _resolve_question_topic_id(q, assessment)
        if not topic_id:
            continue
        bucket = by_topic.setdefault(topic_id, {"correct": 0, "total": 0})
        bucket["total"] += 1
        ans = ans_map.get(q.id)
        if ans and ans.is_correct:
            bucket["correct"] += 1

    now = datetime.utcnow()
    for topic_id, stats in by_topic.items():
        pct = 100.0 * stats["correct"] / stats["total"] if stats["total"] else 0.0
        row = (
            db.query(StudentTopicScore)
            .filter(
                StudentTopicScore.student_id == attempt.student_id,
                StudentTopicScore.topic_id == topic_id,
            )
            .first()
        )
        prev = row.score_percent if row else None
        status = compute_mastery_status(pct, prev)
        if row:
            row.score_percent = pct
            row.mastery_status = status
            row.last_assessed_at = now
        else:
            db.add(
                StudentTopicScore(
                    student_id=attempt.student_id,
                    topic_id=topic_id,
                    score_percent=pct,
                    mastery_status=status,
                    last_assessed_at=now,
                )
            )
    db.commit()


def get_student_mastery(student_id: int) -> List[dict]:
    db = get_db()
    rows = (
        db.query(StudentTopicScore)
        .filter(StudentTopicScore.student_id == student_id)
        .order_by(StudentTopicScore.topic_id)
        .all()
    )
    return [
        {
            "topic_id": r.topic_id,
            "topic_name": _topic_display_name(r.topic_id),
            "score_percent": r.score_percent,
            "mastery_status": r.mastery_status,
            "last_assessed_at": r.last_assessed_at.isoformat() if r.last_assessed_at else None,
        }
        for r in rows
    ]


def get_overall_progress(student_id: int) -> float:
    rows = get_student_mastery(student_id)
    if not rows:
        return 0.0
    return round(sum(r["score_percent"] for r in rows) / len(rows), 2)


def analyze_attempt(attempt_id: int) -> dict:
    db = get_db()
    attempt = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id).first()
    if not attempt:
        raise LMSNotFoundError(f"Attempt {attempt_id} not found")

    assessment = get_assessment(attempt.assessment_id)
    if assessment.assessment_type == "diagnostic":
        from app.services.lms.weakness_analyzer import analyze_diagnostic_attempt

        return analyze_diagnostic_attempt(attempt_id)

    q_ids = [aq.question_id for aq in assessment.questions]
    questions = db.query(Question).filter(Question.id.in_(q_ids)).all()
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt_id).all()
    ans_map = {a.question_id: a for a in answers}

    by_topic: dict[int, dict] = {}
    for q in questions:
        topic_id = _resolve_question_topic_id(q, assessment)
        if not topic_id:
            continue
        bucket = by_topic.setdefault(
            topic_id, {"topic_id": topic_id, "correct": 0, "total": 0}
        )
        bucket["total"] += 1
        ans = ans_map.get(q.id)
        if ans and ans.is_correct:
            bucket["correct"] += 1

    weak, strong = [], []
    for tid, stats in by_topic.items():
        pct = 100.0 * stats["correct"] / stats["total"] if stats["total"] else 0.0
        topic_name = _topic_display_name(tid)
        entry = {"topic_id": tid, "topic_name": topic_name, "score_percent": round(pct, 2)}
        if pct < WEAK_THRESHOLD:
            weak.append(entry)
        elif pct >= 80.0:
            strong.append(entry)

    return {"weak_topics": weak, "strong_topics": strong}


def get_diagnostic_weak_topics(student_id: int, assessment_id: Optional[int] = None) -> List[dict]:
    """Weak areas from the latest submitted diagnostic attempt (AI-derived concepts)."""
    db = get_db()
    q = db.query(AssessmentAttempt).filter(
        AssessmentAttempt.student_id == student_id,
        AssessmentAttempt.status == "submitted",
    )
    if assessment_id:
        q = q.filter(AssessmentAttempt.assessment_id == assessment_id)
    else:
        q = q.join(Assessment, Assessment.id == AssessmentAttempt.assessment_id).filter(
            Assessment.assessment_type == "diagnostic"
        )

    attempt = q.order_by(AssessmentAttempt.submitted_at.desc()).first()
    if not attempt:
        return []

    return analyze_attempt(attempt.id).get("weak_topics") or []


def get_weak_topics_for_student(student_id: int) -> List[dict]:
    weak = get_diagnostic_weak_topics(student_id)
    if weak:
        return weak
    rows = get_student_mastery(student_id)
    return [
        r
        for r in rows
        if r.get("mastery_status") == "weak" or r.get("score_percent", 100) < WEAK_THRESHOLD
    ]


def rebuild_student_mastery(student_id: int) -> None:
    """Recompute topic scores from all submitted attempts (fixes missing topic_id on questions)."""
    db = get_db()
    attempts = (
        db.query(AssessmentAttempt)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.status == "submitted",
        )
        .order_by(AssessmentAttempt.submitted_at.asc())
        .all()
    )
    for attempt in attempts:
        try:
            update_topic_scores_from_attempt(attempt.id)
        except Exception as exc:
            logger.warning(
                "rebuild_student_mastery: attempt %s failed for student %s: %s",
                attempt.id,
                student_id,
                exc,
            )
    db.commit()


def create_snapshot(student_id: int) -> MasterySnapshot:
    db = get_db()
    data = get_student_mastery(student_id)
    snap = MasterySnapshot(student_id=student_id, snapshot_json=json.dumps(data))
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap
