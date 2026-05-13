"""Pass probability, exam confidence, cognitive DNA, causal topics, recommendations."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.phase1_models import SyllabusChapter, SyllabusTopic
from app.models.phase3_models import (
    QuestionBankItem,
    StudentExamTarget,
    StudentLearningPreferences,
    StudentPlanAdherence,
)
from app.models.phase4_models import (
    QuestionPracticeAttempt,
    StudentCognitiveSnapshot,
    StudentConceptSchedule,
    StudentIntelligenceSnapshot,
)
from app.models.school_learning_models import QuizSubmission
from app.services.phase4.constants import (
    PASS_PROBABILITY_THRESHOLD_RECOMMENDATIONS,
    PREDICTION_DISCLAIMER,
)


def _attempt_stats(db: Session, *, student_user_id: int, days: int = 90) -> Dict[str, Any]:
    since = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(QuestionPracticeAttempt)
        .filter(
            QuestionPracticeAttempt.student_user_id == student_user_id,
            QuestionPracticeAttempt.answered_at.isnot(None),
            QuestionPracticeAttempt.answered_at >= since,
        )
        .all()
    )
    total = 0
    correct = 0
    for_prob = 0
    correct_for_prob = 0
    by_topic: Dict[int, List[bool]] = {}
    durations: List[int] = []
    confs: List[int] = []
    guesses = 0
    for r in rows:
        total += 1
        if r.is_correct:
            correct += 1
        if not r.exclude_from_pass_probability:
            for_prob += 1
            if r.is_correct:
                correct_for_prob += 1
        if r.duration_ms is not None:
            durations.append(int(r.duration_ms))
        if r.confidence_before_result is not None:
            confs.append(int(r.confidence_before_result))
        if r.is_guess:
            guesses += 1
        if r.question_bank_item_id:
            item = db.query(QuestionBankItem).filter(QuestionBankItem.id == r.question_bank_item_id).first()
            tid = item.syllabus_topic_id if item and item.syllabus_topic_id else 0
            by_topic.setdefault(tid, []).append(bool(r.is_correct))

    topic_accuracy: Dict[str, float] = {}
    topic_counts: Dict[str, int] = {}
    for tid, arr in by_topic.items():
        if tid == 0:
            continue
        topic_counts[str(tid)] = len(arr)
        topic_accuracy[str(tid)] = sum(1 for x in arr if x) / max(1, len(arr))

    return {
        "total_attempts": total,
        "accuracy": (correct / total) if total else 0.0,
        "accuracy_for_probability": (correct_for_prob / for_prob) if for_prob else 0.0,
        "topic_accuracy": topic_accuracy,
        "topic_counts": topic_counts,
        "durations_ms": durations,
        "confidences": confs,
        "guess_rate": (guesses / total) if total else 0.0,
    }


def _syllabus_topic_total(db: Session, *, exam_type_id: Optional[int]) -> int:
    if not exam_type_id:
        return 1
    return (
        db.query(func.count(SyllabusTopic.id))
        .join(SyllabusChapter, SyllabusChapter.id == SyllabusTopic.chapter_id)
        .filter(SyllabusChapter.exam_type_id == int(exam_type_id), SyllabusTopic.is_active.is_(True))
        .scalar()
        or 1
    )


def _adherence_ratio(db: Session, *, student_user_id: int, days: int = 14) -> float:
    start = date.today() - timedelta(days=days)
    rows = (
        db.query(StudentPlanAdherence)
        .filter(
            StudentPlanAdherence.student_user_id == student_user_id,
            StudentPlanAdherence.day >= start,
        )
        .all()
    )
    if not rows:
        return 0.7
    missed = sum(1 for r in rows if r.missed)
    return max(0.0, 1.0 - (missed / max(1, len(rows))))


def _quiz_ratio(db: Session, *, student_user_id: int) -> Optional[float]:
    rows = (
        db.query(QuizSubmission)
        .filter(QuizSubmission.student_user_id == student_user_id, QuizSubmission.score.isnot(None))
        .order_by(QuizSubmission.submitted_at.desc())
        .limit(15)
        .all()
    )
    if not rows:
        return None
    ratios: List[float] = []
    for r in rows:
        if r.max_score and float(r.max_score) > 0 and r.score is not None:
            ratios.append(float(r.score) / float(r.max_score))
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def compute_cognitive_dna(db: Session, *, student_user_id: int) -> Dict[str, Any]:
    st = _attempt_stats(db, student_user_id=student_user_id)
    durs = st["durations_ms"]
    median_ms = sorted(durs)[len(durs) // 2] if durs else None
    confs = st["confidences"]
    avg_conf = sum(confs) / len(confs) if confs else None
    calibration = None
    if confs and st["total_attempts"]:
        calibration = max(0.0, min(1.0, st["accuracy"] / max(0.01, (avg_conf or 3) / 5.0)))

    return {
        "accuracy_overall": round(st["accuracy"], 4),
        "accuracy_for_predictions": round(st["accuracy_for_probability"], 4),
        "median_response_ms": median_ms,
        "avg_confidence_1_5": round(avg_conf, 3) if avg_conf is not None else None,
        "calibration_score": round(calibration, 4) if calibration is not None else None,
        "guess_rate": round(st["guess_rate"], 4),
        "topics_practiced": len(st["topic_accuracy"]),
        "learning_consistency": round(_adherence_ratio(db, student_user_id=student_user_id), 4),
    }


def radar_payloads(dna: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Student-positive labels vs raw axes (same underlying 0-1 normalized)."""
    axes = {
        "accuracy": min(1.0, float(dna.get("accuracy_overall") or 0)),
        "consistency": min(1.0, float(dna.get("learning_consistency") or 0)),
        "calibration": float(dna.get("calibration_score") or 0.5),
        "speed_control": 1.0 - min(1.0, (float(dna.get("median_response_ms") or 60000) / 120000.0)),
        "confidence": min(1.0, (float(dna.get("avg_confidence_1_5") or 3) / 5.0)),
        "integrity": 1.0 - min(1.0, float(dna.get("guess_rate") or 0) * 2),
    }
    student_labels = {
        "accuracy": "Mastery momentum",
        "consistency": "Study rhythm",
        "calibration": "Self-awareness",
        "speed_control": "Thoughtful pacing",
        "confidence": "Courage to try",
        "integrity": "Authentic effort",
    }
    return (
        {"axes": axes, "labels": student_labels, "positive_framing": True},
        {"axes": axes, "positive_framing": False},
    )


def _weak_topics(topic_accuracy: Dict[str, float], topic_counts: Dict[str, int]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for tid, acc in topic_accuracy.items():
        n = topic_counts.get(tid, 0)
        if n >= 2 and acc < 0.55:
            out.append({"syllabus_topic_id": int(tid), "accuracy": acc, "attempts": n})
    out.sort(key=lambda x: x["accuracy"])
    return out


def _causal_topics(
    *,
    weak: List[Dict[str, Any]],
    base_prob: float,
    topic_accuracy: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Marginal impact if topic accuracy hypothetically raised to 0.85."""
    ranked: List[Dict[str, Any]] = []
    for w in weak[:15]:
        tid = str(w["syllabus_topic_id"])
        acc = float(topic_accuracy.get(tid, 0.4))
        boosted = base_prob + (0.85 - acc) * 0.12
        ranked.append(
            {
                "syllabus_topic_id": int(tid),
                "current_accuracy": round(acc, 4),
                "estimated_impact_on_pass_probability": round(max(0.0, boosted - base_prob), 4),
            }
        )
    ranked.sort(key=lambda x: -x["estimated_impact_on_pass_probability"])
    return ranked[:10]


def _three_recommendations(
    *,
    weak: List[Dict[str, Any]],
    days_to_exam: Optional[int],
) -> List[Dict[str, Any]]:
    deadline = (date.today() + timedelta(days=min(7, days_to_exam or 7))).isoformat()
    recs: List[Dict[str, Any]] = []
    if weak:
        top = weak[0]["syllabus_topic_id"]
        recs.append(
            {
                "task": f"Complete a recovery bundle for syllabus topic #{top}",
                "deadline": deadline,
                "estimated_improvement_pct": 8.0,
                "cta": "/student-learning/phase4-recovery",
                "status": "open",
            }
        )
    if len(weak) > 1:
        t2 = weak[1]["syllabus_topic_id"]
        recs.append(
            {
                "task": f"Schedule 20 minutes of targeted practice on topic #{t2}",
                "deadline": deadline,
                "estimated_improvement_pct": 5.0,
                "cta": "/student-learning/phase4-practice",
                "status": "open",
            }
        )
    recs.append(
        {
            "task": "Enable daily micro-revision for your weakest concepts",
            "deadline": deadline,
            "estimated_improvement_pct": 4.0,
            "cta": "/student-learning/phase4-micro",
            "status": "open",
        }
    )
    while len(recs) < 3:
        recs.append(
            {
                "task": "Review your study plan and mark today's blocks complete",
                "deadline": deadline,
                "estimated_improvement_pct": 3.0,
                "cta": "/student-learning/hub",
                "status": "open",
            }
        )
    return recs[:3]


def _two_future(base_prob: float, marks_mid: float) -> Dict[str, Any]:
    plan_prob = min(0.99, base_prob + 0.12)
    plan_marks = min(100.0, marks_mid + 8.0)
    return {
        "current_path": {
            "pass_probability": round(base_prob, 4),
            "predicted_marks_mid": round(marks_mid, 1),
        },
        "ai_plan_path": {
            "pass_probability": round(plan_prob, 4),
            "predicted_marks_mid": round(plan_marks, 1),
        },
        "probability_delta": round(plan_prob - base_prob, 4),
        "recommended_cta": "Follow your next three recommended actions this week.",
    }


def compute_intelligence_snapshot(
    db: Session,
    *,
    student_user_id: int,
    exam_target_id: Optional[int] = None,
) -> StudentIntelligenceSnapshot:
    target = None
    if exam_target_id:
        target = (
            db.query(StudentExamTarget)
            .filter(
                StudentExamTarget.id == int(exam_target_id),
                StudentExamTarget.student_user_id == student_user_id,
            )
            .first()
        )
    if not target:
        target = (
            db.query(StudentExamTarget)
            .filter(StudentExamTarget.student_user_id == student_user_id)
            .order_by(StudentExamTarget.exam_date.asc())
            .first()
        )

    st = _attempt_stats(db, student_user_id=student_user_id)
    et_id = target.id if target else None
    exam_type_id = target.exam_type_id if target else None
    total_topics = _syllabus_topic_total(db, exam_type_id=exam_type_id)
    practiced = len([t for t in st["topic_accuracy"].keys() if int(t) > 0])
    coverage = min(1.0, practiced / max(1, total_topics))
    adhere = _adherence_ratio(db, student_user_id=student_user_id)
    acc = st["accuracy_for_probability"]
    quiz_r = _quiz_ratio(db, student_user_id=student_user_id)
    weak = _weak_topics(st["topic_accuracy"], st["topic_counts"])

    prefs = db.query(StudentLearningPreferences).filter_by(student_user_id=student_user_id).first()
    streak = int(prefs.streak_days) if prefs else 0

    base = (
        0.28
        + 0.32 * acc
        + 0.18 * coverage
        + 0.12 * adhere
        + 0.05 * min(1.0, streak / 21.0)
        - 0.08 * st["guess_rate"]
    )
    if quiz_r is not None:
        base += 0.05 * (quiz_r - 0.5)
    base -= min(0.2, len(weak) * 0.025)
    pass_prob = max(0.03, min(0.97, base))

    days_to_exam = None
    if target and target.exam_date:
        days_to_exam = (target.exam_date - date.today()).days

    exam_conf = max(
        0.05,
        min(
            0.98,
            0.25 * coverage + 0.35 * (quiz_r or acc) + 0.25 * adhere + 0.15 * (1.0 - st["guess_rate"]),
        ),
    )

    marks_mid = max(15.0, min(95.0, 35 + 45 * acc + 15 * coverage + 5 * adhere))
    marks_low = max(0.0, marks_mid - 8.0)
    marks_high = min(100.0, marks_mid + 7.0)

    causal = _causal_topics(weak=weak, base_prob=pass_prob, topic_accuracy=st["topic_accuracy"])
    recs = (
        _three_recommendations(weak=weak, days_to_exam=days_to_exam)
        if pass_prob < PASS_PROBABILITY_THRESHOLD_RECOMMENDATIONS
        else []
    )
    two_f = _two_future(pass_prob, marks_mid)

    inputs: Dict[str, Any] = {
        "accuracy_for_probability": acc,
        "syllabus_coverage": coverage,
        "adherence_ratio": adhere,
        "streak_days": streak,
        "guess_rate": st["guess_rate"],
        "weak_topic_count": len(weak),
        "quiz_avg_ratio": quiz_r,
        "practiced_topics": practiced,
        "total_syllabus_topics": total_topics,
        "days_to_exam": days_to_exam,
    }

    risk = "low"
    if pass_prob < 0.45:
        risk = "high"
    elif pass_prob < 0.6:
        risk = "medium"

    snap = StudentIntelligenceSnapshot(
        student_user_id=student_user_id,
        exam_target_id=et_id,
        pass_probability=pass_prob,
        exam_confidence=exam_conf,
        marks_low=marks_low,
        marks_high=marks_high,
        inputs_json=json.dumps(inputs, default=str),
        causal_topics_json=json.dumps(causal, default=str),
        recommendations_json=json.dumps(recs, default=str),
        two_future_json=json.dumps(two_f, default=str),
        days_to_exam=days_to_exam,
        risk_urgency=risk,
        computed_at=datetime.utcnow(),
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def persist_cognitive_snapshot(db: Session, *, student_user_id: int) -> StudentCognitiveSnapshot:
    dna = compute_cognitive_dna(db, student_user_id=student_user_id)
    r_student, r_raw = radar_payloads(dna)
    row = StudentCognitiveSnapshot(
        student_user_id=student_user_id,
        snapshot_at=datetime.utcnow(),
        dna_json=json.dumps(dna, default=str),
        radar_student_json=json.dumps(r_student, default=str),
        radar_raw_json=json.dumps(r_raw, default=str),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def retention_map(db: Session, *, student_user_id: int) -> List[Dict[str, Any]]:
    """Per concept/topic retention tier for UI."""
    rows = (
        db.query(StudentConceptSchedule)
        .filter(StudentConceptSchedule.student_user_id == student_user_id)
        .all()
    )
    now = datetime.utcnow()
    out: List[Dict[str, Any]] = []
    for r in rows:
        tier = "fading"
        label = "Keep sharpening"
        strength = float(r.strength_estimate or 0.5)
        if r.next_review_at and r.next_review_at < now:
            tier = "weak"
            label = "Ready for a friendly refresher"
        elif strength >= 0.72:
            tier = "strong"
            label = "Solid strength here"
        out.append(
            {
                "concept_key": r.concept_key,
                "syllabus_topic_id": r.syllabus_topic_id,
                "tier": tier,
                "student_label": label,
                "next_review_at": r.next_review_at.isoformat() if r.next_review_at else None,
                "strength_estimate": strength,
            }
        )
    return out


def latest_snapshot_dict(db: Session, *, student_user_id: int) -> Optional[Dict[str, Any]]:
    row = (
        db.query(StudentIntelligenceSnapshot)
        .filter(StudentIntelligenceSnapshot.student_user_id == student_user_id)
        .order_by(StudentIntelligenceSnapshot.computed_at.desc())
        .first()
    )
    if not row:
        return None
    return {
        "pass_probability": float(row.pass_probability) if row.pass_probability is not None else None,
        "exam_confidence": float(row.exam_confidence) if row.exam_confidence is not None else None,
        "marks_low": float(row.marks_low) if row.marks_low is not None else None,
        "marks_high": float(row.marks_high) if row.marks_high is not None else None,
        "inputs": json.loads(row.inputs_json) if row.inputs_json else {},
        "causal_topics": json.loads(row.causal_topics_json) if row.causal_topics_json else [],
        "recommendations": json.loads(row.recommendations_json) if row.recommendations_json else [],
        "two_future": json.loads(row.two_future_json) if row.two_future_json else {},
        "days_to_exam": row.days_to_exam,
        "risk_urgency": row.risk_urgency,
        "computed_at": row.computed_at.isoformat() if row.computed_at else None,
        "prediction_disclaimer": PREDICTION_DISCLAIMER,
    }


def disclaimer() -> str:
    return PREDICTION_DISCLAIMER


def upsert_exam_confidence_daily(
    db: Session,
    *,
    student_user_id: int,
    exam_target_id: Optional[int],
    score: float,
    factors: Optional[Dict[str, Any]] = None,
) -> None:
    from app.models.phase4_models import ExamConfidenceDaily

    today = date.today()
    row = (
        db.query(ExamConfidenceDaily)
        .filter(
            ExamConfidenceDaily.student_user_id == student_user_id,
            ExamConfidenceDaily.exam_target_id == exam_target_id,
            ExamConfidenceDaily.day == today,
        )
        .first()
    )
    if not row:
        row = ExamConfidenceDaily(
            student_user_id=student_user_id,
            exam_target_id=exam_target_id,
            day=today,
            score=score,
            factors_json=json.dumps(factors or {}, default=str),
        )
        db.add(row)
    else:
        row.score = score
        row.factors_json = json.dumps(factors or {}, default=str)
    db.commit()
