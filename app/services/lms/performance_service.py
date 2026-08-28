"""Student performance and mastery tracking."""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from app.models.lms_models import AssessmentAttempt, AttemptAnswer, MasterySnapshot, Question, StudentTopicScore
from app.services.lms.assessment_service import get_assessment
from app.services.lms.exceptions import LMSNotFoundError
from app.utils.db import get_db

MASTERED_THRESHOLD = 85.0
WEAK_THRESHOLD = 60.0


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
    q_ids = [aq.question_id for aq in assessment.questions]
    questions = db.query(Question).filter(Question.id.in_(q_ids)).all()
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt_id).all()
    ans_map = {a.question_id: a for a in answers}

    by_topic: dict[int, dict] = {}
    for q in questions:
        if not q.topic_id:
            continue
        bucket = by_topic.setdefault(q.topic_id, {"correct": 0, "total": 0})
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
    q_ids = [aq.question_id for aq in assessment.questions]
    questions = db.query(Question).filter(Question.id.in_(q_ids)).all()
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt_id).all()
    ans_map = {a.question_id: a for a in answers}

    by_topic: dict[int, dict] = {}
    for q in questions:
        if not q.topic_id:
            continue
        bucket = by_topic.setdefault(
            q.topic_id, {"topic_id": q.topic_id, "correct": 0, "total": 0}
        )
        bucket["total"] += 1
        ans = ans_map.get(q.id)
        if ans and ans.is_correct:
            bucket["correct"] += 1

    weak, strong = [], []
    for tid, stats in by_topic.items():
        pct = 100.0 * stats["correct"] / stats["total"] if stats["total"] else 0.0
        entry = {"topic_id": tid, "score_percent": round(pct, 2)}
        if pct < WEAK_THRESHOLD:
            weak.append(entry)
        elif pct >= 80.0:
            strong.append(entry)

    return {"weak_topics": weak, "strong_topics": strong}


def create_snapshot(student_id: int) -> MasterySnapshot:
    db = get_db()
    data = get_student_mastery(student_id)
    snap = MasterySnapshot(student_id=student_id, snapshot_json=json.dumps(data))
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap
