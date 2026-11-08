"""Class and student analytics (Phase 7)."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from app.models.database_models import User as DBUser
from app.models.lms_models import (
    Assessment,
    AssessmentAttempt,
    Assignment,
    AssignmentSubmission,
    MasterySnapshot,
    Question,
    StudentTopicScore,
    Topic,
)
from app.services.lms.class_service import list_class_students, teacher_owns_class
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.performance_service import (
    MASTERED_THRESHOLD,
    WEAK_THRESHOLD,
    get_overall_progress,
    get_student_mastery,
    is_weak_mastery,
)
from app.utils.db import get_db


def aggregate_class_topics(class_id: int, teacher_id: int) -> List[dict]:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    enrollments = list_class_students(class_id)
    student_ids = [e.student_id for e in enrollments]
    if not student_ids:
        return []

    rows = (
        db.query(StudentTopicScore)
        .filter(StudentTopicScore.student_id.in_(student_ids))
        .all()
    )
    by_topic: dict[int, dict] = {}
    for r in rows:
        bucket = by_topic.setdefault(
            r.topic_id,
            {"topic_id": r.topic_id, "scores": [], "weak_students": []},
        )
        bucket["scores"].append(r.score_percent)
        if r.score_percent < WEAK_THRESHOLD:
            bucket["weak_students"].append(
                {"student_id": r.student_id, "score_percent": r.score_percent}
            )

    weak_student_ids = {
        s["student_id"] for stats in by_topic.values() for s in stats["weak_students"]
    }
    users = (
        {
            u.id: u
            for u in db.query(DBUser).filter(DBUser.id.in_(weak_student_ids)).all()
        }
        if weak_student_ids
        else {}
    )

    topics = {t.id: t for t in db.query(Topic).filter(Topic.id.in_(by_topic.keys())).all()}
    result = []
    for tid, stats in by_topic.items():
        topic = topics.get(tid)
        avg = sum(stats["scores"]) / len(stats["scores"]) if stats["scores"] else 0
        weak_students = []
        for s in stats["weak_students"]:
            user = users.get(s["student_id"])
            weak_students.append(
                {
                    "student_id": s["student_id"],
                    "username": user.username if user else f"Student #{s['student_id']}",
                    "score_percent": round(s["score_percent"], 1),
                }
            )
        weak_students.sort(key=lambda x: (x["username"] or "").lower())
        result.append(
            {
                "topic_id": tid,
                "topic_name": topic.name if topic else f"Topic #{tid}",
                "avg_score": round(avg, 2),
                "weak_student_count": len(weak_students),
                "weak_students": weak_students,
                "students_assessed": len(set(student_ids)),
            }
        )
    result.sort(key=lambda x: x["avg_score"])
    return result


def get_class_quiz_results(class_id: int, teacher_id: int) -> List[dict]:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    assignments = (
        db.query(Assignment)
        .filter(Assignment.class_id == class_id, Assignment.status == "published")
        .all()
    )
    enrollments = list_class_students(class_id)
    total_students = len(enrollments)
    student_ids = [e.student_id for e in enrollments]
    users = (
        {
            u.id: u
            for u in db.query(DBUser).filter(DBUser.id.in_(student_ids)).all()
        }
        if student_ids
        else {}
    )
    results = []
    for a in assignments:
        subs = (
            db.query(AssignmentSubmission)
            .filter(AssignmentSubmission.assignment_id == a.id)
            .all()
        )
        subs_by_student = {s.student_id: s for s in subs}
        attempt_ids = [s.attempt_id for s in subs if s.attempt_id]
        attempts = (
            {
                att.id: att
                for att in db.query(AssessmentAttempt)
                .filter(AssessmentAttempt.id.in_(attempt_ids))
                .all()
            }
            if attempt_ids
            else {}
        )
        submitted = [s for s in subs if s.status == "submitted"]
        scores = []
        student_results = []
        for student_id in student_ids:
            user = users.get(student_id)
            sub = subs_by_student.get(student_id)
            status = sub.status if sub else "not_started"
            score = None
            max_score = None
            score_percent = None
            if sub and sub.attempt_id:
                att = attempts.get(sub.attempt_id)
                if att:
                    score = att.score
                    max_score = att.max_score
                    if att.max_score:
                        score_percent = round(100.0 * (att.score or 0) / att.max_score, 1)
                        if sub.status == "submitted":
                            scores.append(score_percent)
            student_results.append(
                {
                    "student_id": student_id,
                    "username": user.username if user else f"Student #{student_id}",
                    "status": status,
                    "score": score,
                    "max_score": max_score,
                    "score_percent": score_percent,
                }
            )
        student_results.sort(key=lambda x: (x["username"] or "").lower())
        results.append(
            {
                "assignment_id": a.id,
                "title": a.title,
                "quiz_id": a.quiz_id,
                "due_date": a.due_date.isoformat() if a.due_date else None,
                "total_students": total_students,
                "submitted_count": len(submitted),
                "completion_percent": round(100.0 * len(submitted) / total_students, 1) if total_students else 0,
                "avg_score_percent": round(sum(scores) / len(scores), 1) if scores else None,
                "overdue_count": max(0, total_students - len(submitted)),
                "student_results": student_results,
            }
        )
    return results


def get_class_roster_summary(class_id: int, teacher_id: int) -> List[dict]:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    enrollments = list_class_students(class_id)
    roster = []
    for enr in enrollments:
        user = db.query(DBUser).filter(DBUser.id == enr.student_id).first()
        mastery = get_student_mastery(enr.student_id)
        # Prefer live mastery rows that are not fully mastered (any miss = practice).
        weak = [m for m in mastery if is_weak_mastery(m)]
        weak_topics = [
            {
                "topic_id": m["topic_id"],
                "topic_name": m["topic_name"],
                "score_percent": m.get("score_percent"),
            }
            for m in weak
        ]
        weak_topics.sort(key=lambda x: (x["topic_name"] or "").lower())
        progress = get_overall_progress(enr.student_id)
        roster.append(
            {
                "student_id": enr.student_id,
                "username": user.username if user else None,
                "full_name": (getattr(user, "full_name", None) or user.username) if user else None,
                "email": user.useremail if user else None,
                "overall_progress": progress,
                "weak_topic_count": len(weak_topics),
                "weak_topics": weak_topics,
                "has_mastery_data": bool(mastery),
                # Still learning until 100% mastery — no 60% "on track" rule.
                "is_struggling": bool(mastery) and (
                    len(weak_topics) > 0 or (progress is not None and progress < MASTERED_THRESHOLD)
                ),
                "enrolled_at": enr.enrolled_at.isoformat() if enr.enrolled_at else None,
            }
        )
    return roster


def get_student_report(student_id: int, teacher_id: int, class_id: int) -> dict:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    if not any(e.student_id == student_id for e in list_class_students(class_id)):
        raise LMSNotFoundError("Student not in class")
    user = db.query(DBUser).filter(DBUser.id == student_id).first()
    mastery = get_student_mastery(student_id)
    attempts = (
        db.query(AssessmentAttempt)
        .filter(AssessmentAttempt.student_id == student_id, AssessmentAttempt.status == "submitted")
        .order_by(AssessmentAttempt.submitted_at.desc())
        .limit(20)
        .all()
    )
    return {
        "student_id": student_id,
        "username": user.username if user else None,
        "overall_progress": get_overall_progress(student_id),
        "topics": mastery,
        "recent_attempts": [
            {
                "attempt_id": a.id,
                "assessment_id": a.assessment_id,
                "score_percent": round(100.0 * (a.score or 0) / (a.max_score or 1), 1),
                "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            }
            for a in attempts
        ],
    }


def get_struggling_students(class_id: int, teacher_id: int, threshold: float = WEAK_THRESHOLD) -> List[dict]:
    roster = get_class_roster_summary(class_id, teacher_id)
    return [
        r for r in roster
        if r.get("is_struggling") or (r.get("has_mastery_data") and r.get("overall_progress", 100) < threshold)
    ]


def get_progress_over_time(student_id: int) -> List[dict]:
    db = get_db()
    snaps = (
        db.query(MasterySnapshot)
        .filter(MasterySnapshot.student_id == student_id)
        .order_by(MasterySnapshot.created_at)
        .limit(50)
        .all()
    )
    import json

    return [
        {"created_at": s.created_at.isoformat(), "snapshot": json.loads(s.snapshot_json)}
        for s in snaps
    ]


def get_topic_assessment_series(student_id: int) -> dict:
    """Per-topic scores across diagnostic + quiz attempts (chronological).

    Powers the teacher Topic Progress chart: X = assessments that touched
    the topic (dynamic), Y = score_percent — so teachers can see topic-wise
    improvement over time.
    """
    from app.services.lms.performance_service import topic_breakdown_for_attempt

    db = get_db()
    rows = (
        db.query(AssessmentAttempt, Assessment)
        .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.status == "submitted",
            Assessment.assessment_type.in_(("diagnostic", "quiz")),
        )
        .order_by(
            AssessmentAttempt.submitted_at.asc(),
            AssessmentAttempt.id.asc(),
        )
        .all()
    )

    by_topic: dict[int, dict] = {}
    for attempt, assessment in rows:
        breakdown = topic_breakdown_for_attempt(attempt.id)
        if not breakdown:
            continue
        if assessment.assessment_type == "diagnostic":
            label = "Diagnostic"
        else:
            label = (assessment.title or "").strip() or f"Quiz #{assessment.id}"
        for row in breakdown:
            tid = row.get("topic_id")
            if not tid:
                continue
            bucket = by_topic.setdefault(
                tid,
                {
                    "topic_id": tid,
                    "topic_name": row.get("topic_name") or f"Topic #{tid}",
                    "series": [],
                },
            )
            if not bucket.get("topic_name") and row.get("topic_name"):
                bucket["topic_name"] = row["topic_name"]
            bucket["series"].append(
                {
                    "assessment_id": assessment.id,
                    "attempt_id": attempt.id,
                    "label": label,
                    "assessment_type": assessment.assessment_type,
                    "correct": row.get("correct"),
                    "total": row.get("total"),
                    "score_percent": row.get("score_percent"),
                    "submitted_at": (
                        attempt.submitted_at.isoformat() if attempt.submitted_at else None
                    ),
                }
            )

    topics = sorted(by_topic.values(), key=lambda t: (t.get("topic_name") or "").lower())
    return {"topics": topics}


def get_student_topic_progress_for_teacher(
    teacher_id: int, class_id: int, student_id: int
) -> dict:
    """Topic-wise assessment history for one enrolled student (teacher analytics)."""
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    if not any(e.student_id == student_id for e in list_class_students(class_id)):
        raise LMSNotFoundError("Student not in class")
    return get_topic_assessment_series(student_id)


def pdf_source_analytics(teacher_id: int) -> dict:
    db = get_db()
    pdf_qs = (
        db.query(Question)
        .filter(Question.created_by == teacher_id, Question.source_type == "pdf_qa_auto")
        .count()
    )
    manual_qs = (
        db.query(Question)
        .filter(Question.created_by == teacher_id, Question.source_type == "manual")
        .count()
    )
    pdf_quizzes = (
        db.query(Assessment)
        .filter(Assessment.created_by == teacher_id, Assessment.creation_mode == "pdf_qa_auto")
        .count()
    )
    manual_quizzes = (
        db.query(Assessment)
        .filter(Assessment.created_by == teacher_id, Assessment.creation_mode == "manual")
        .count()
    )
    return {
        "pdf_questions": pdf_qs,
        "manual_questions": manual_qs,
        "pdf_quizzes": pdf_quizzes,
        "manual_quizzes": manual_quizzes,
    }


def export_class_csv(class_id: int, teacher_id: int) -> str:
    roster = get_class_roster_summary(class_id, teacher_id)
    lines = ["student_id,username,overall_progress,weak_topics,struggling"]
    for r in roster:
        lines.append(
            f"{r['student_id']},{r.get('username','')},{r.get('overall_progress',0)},"
            f"{r.get('weak_topic_count',0)},{r.get('is_struggling',False)}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Domain -> Subtopic drill-down (additive; Domain analytics above are unchanged)
# ---------------------------------------------------------------------------

# A subtopic average built from fewer answers than this is shown as low-evidence.
MIN_SUBTOPIC_EVIDENCE = 3


def aggregate_class_subtopics(class_id: int, teacher_id: int, domain_topic_id: Optional[int] = None) -> dict:
    """Class performance per Domain and Subtopic.

    Aggregation policy (documented in docs/SUBTOPIC_LEARNING_SYSTEM.md):
    * ``class_average`` - mean of each assessed student's current subtopic score
      (same convention as the existing Domain ``avg_score``).
    * ``question_weighted_percent`` - total correct / total answered in the
      evidence behind those scores, so subtopics with different question counts
      are not averaged blindly.
    * Domain rows: ``class_average`` is the existing Domain mastery average from
      StudentTopicScore (unchanged); ``subtopic_question_weighted_percent`` is
      the question-weighted roll-up of its subtopics.
    * ``baseline_average`` / ``latest_assessment_average`` compare the first
      diagnostic with the most recent Baseline/Follow-up per student.
    """
    from app.models.lms_models import (
        AttemptAnswer,
        QuestionTaxonomy,
        StudentSubtopicScore,
    )
    from app.services.lms import guided_practice_service, taxonomy_service

    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    student_ids = [e.student_id for e in list_class_students(class_id)]
    if not student_ids:
        return {"domains": [], "mapping_coverage": {}, "coverage_gaps": []}

    q = db.query(StudentSubtopicScore).filter(StudentSubtopicScore.student_id.in_(student_ids))
    if domain_topic_id:
        q = q.filter(StudentSubtopicScore.domain_topic_id == domain_topic_id)
    rows = q.all()
    names = taxonomy_service.subtopic_names(r.subtopic_id for r in rows)
    users = {u.id: u for u in db.query(DBUser).filter(DBUser.id.in_(student_ids)).all()}

    domain_scores: dict[int, list] = {}
    for r in db.query(StudentTopicScore).filter(StudentTopicScore.student_id.in_(student_ids)).all():
        domain_scores.setdefault(r.topic_id, []).append(r.score_percent)

    subs: dict[int, dict] = {}
    for r in rows:
        info = names.get(r.subtopic_id) or {}
        s = subs.setdefault(
            r.subtopic_id,
            {
                "subtopic_id": r.subtopic_id,
                "subtopic_name": info.get("subtopic_name") or f"Subtopic #{r.subtopic_id}",
                "domain_topic_id": r.domain_topic_id or info.get("domain_topic_id"),
                "domain_name": info.get("domain_name"),
                "_scores": [], "_correct": 0, "_total": 0, "_baseline": [], "_latest": [],
                "weak_students": [],
            },
        )
        s["_scores"].append(r.score_percent)
        s["_correct"] += r.correct_count or 0
        s["_total"] += r.total_count or 0
        if r.baseline_percent is not None:
            s["_baseline"].append(r.baseline_percent)
        if r.latest_assessment_percent is not None:
            s["_latest"].append(r.latest_assessment_percent)
        if r.score_percent < WEAK_THRESHOLD:
            user = users.get(r.student_id)
            s["weak_students"].append(
                {
                    "student_id": r.student_id,
                    "username": user.username if user else f"Student #{r.student_id}",
                    "score_percent": round(r.score_percent, 1),
                }
            )

    def avg(values):
        return round(sum(values) / len(values), 1) if values else None

    domains: dict[int, dict] = {}
    for s in subs.values():
        did = s["domain_topic_id"] or 0
        d = domains.setdefault(
            did,
            {
                "domain_topic_id": did,
                "domain_name": s["domain_name"] or f"Domain #{did}",
                "class_average": avg(domain_scores.get(did, [])),
                "_correct": 0, "_total": 0,
                "subtopics": [],
            },
        )
        d["_correct"] += s["_correct"]
        d["_total"] += s["_total"]
        baseline, latest = avg(s["_baseline"]), avg(s["_latest"])
        s["weak_students"].sort(key=lambda x: (x["username"] or "").lower())
        d["subtopics"].append(
            {
                "subtopic_id": s["subtopic_id"],
                "subtopic_name": s["subtopic_name"],
                "class_average": avg(s["_scores"]),
                "question_weighted_percent": round(100.0 * s["_correct"] / s["_total"], 1) if s["_total"] else None,
                "answers_counted": s["_total"],
                "students_assessed": len(s["_scores"]),
                "weak_student_count": len(s["weak_students"]),
                "weak_students": s["weak_students"],
                "baseline_average": baseline,
                "latest_assessment_average": latest,
                "change_since_baseline": round(latest - baseline, 1) if baseline is not None and latest is not None else None,
                "insufficient_data": s["_total"] < MIN_SUBTOPIC_EVIDENCE,
            }
        )
    breakdowns = parameter_breakdowns(student_ids, users)
    empty = {"cognitive_levels": [], "difficulty_levels": [], "unlabelled": {"cognitive": 0, "difficulty": 0}}
    out = []
    for d in domains.values():
        d.update(breakdowns.get(("d", d["domain_topic_id"]), empty))
        for s in d["subtopics"]:
            s.update(breakdowns.get(("s", s["subtopic_id"]), empty))
        d["subtopic_question_weighted_percent"] = (
            round(100.0 * d.pop("_correct") / d["_total"], 1) if d["_total"] else None
        )
        d["answers_counted"] = d.pop("_total")
        d["subtopics"].sort(key=lambda x: (x["class_average"] is None, x["class_average"] or 0))
        out.append(d)
    out.sort(key=lambda x: (x["class_average"] is None, x["class_average"] or 0))

    # How many answered questions could be mapped to a Subtopic (coverage / gaps).
    from app.models.lms_models import AssessmentAttempt as _Attempt

    coverage_rows = (
        db.query(QuestionTaxonomy.mapping_status, func_count(AttemptAnswer.id))
        .join(AttemptAnswer, AttemptAnswer.question_id == QuestionTaxonomy.question_id)
        .join(_Attempt, _Attempt.id == AttemptAnswer.attempt_id)
        .filter(_Attempt.student_id.in_(student_ids), _Attempt.status == "submitted")
        .group_by(QuestionTaxonomy.mapping_status)
        .all()
    )
    try:
        gaps = guided_practice_service.coverage_gaps_for_students(student_ids)
    except Exception:  # noqa: BLE001
        gaps = []
    return {
        "domains": out,
        "mapping_coverage": {status: int(n) for status, n in coverage_rows},
        "coverage_gaps": gaps,
        "policy": "subtopic_v1",
        "parameter_policy": "four_parameter_v1",
    }


def _level_rows(acc: dict, order: tuple) -> List[dict]:
    """Finalize one breakdown ({level: accumulator}) into display rows, real data only."""
    rows = []
    for level in sorted(acc, key=lambda lv: (order.index(lv) if lv in order else len(order), lv)):
        a = acc[level]
        base_c = base_t = fu_c = fu_t = 0
        weak = []
        for sid, st in a["students"].items():
            latest = st["attempts"][st["latest"]]
            if latest[0] < latest[1]:
                weak.append({"student_id": sid, "username": st["username"], "correct": latest[0], "total": latest[1]})
            if st["baseline"] is not None and st["latest"] != st["baseline"]:
                b = st["attempts"][st["baseline"]]
                base_c, base_t = base_c + b[0], base_t + b[1]
                fu_c, fu_t = fu_c + latest[0], fu_t + latest[1]
        baseline_pct = round(100.0 * base_c / base_t, 1) if base_t else None
        follow_pct = round(100.0 * fu_c / fu_t, 1) if fu_t else None
        weak.sort(key=lambda w: (w["username"] or "").lower())
        rows.append(
            {
                "level": level,
                "correct": a["correct"],
                "incorrect": a["total"] - a["correct"],
                "answers_counted": a["total"],
                "percent": round(100.0 * a["correct"] / a["total"], 1) if a["total"] else None,
                "students_assessed": len(a["students"]),
                "weak_student_count": len(weak),
                "weak_students": weak,
                # Same students on both sides: their first Baseline vs their latest Follow-up.
                "students_with_follow_up": sum(
                    1 for st in a["students"].values() if st["baseline"] is not None and st["latest"] != st["baseline"]
                ),
                "baseline_percent": baseline_pct,
                "follow_up_percent": follow_pct,
                "change_since_baseline": (
                    round(follow_pct - baseline_pct, 1) if baseline_pct is not None and follow_pct is not None else None
                ),
                "guided_practice_correct": a["gp_correct"],
                "guided_practice_answered": a["gp_total"],
                "insufficient_data": a["total"] < MIN_SUBTOPIC_EVIDENCE,
            }
        )
    return rows


def parameter_breakdowns(student_ids: List[int], users: dict) -> dict:
    """Cognitive-level and difficulty performance per Subtopic and per Domain.

    Source: ``lms_subtopic_evidence`` rows of submitted Baseline/Follow-up attempts
    (the same answers subtopic mastery is built from) joined to the answered
    question's stored cognitive level / *printed* difficulty. Guided Practice
    answers are counted separately and never mixed into assessment percentages.
    Questions whose paper printed no cognitive level / difficulty are counted in
    ``unlabelled`` instead of being guessed.
    """
    from app.models.lms_models import GuidedPracticeAttempt, SubtopicEvidence, TargetBankItem
    from app.services.lms import question_attributes as qa
    from app.services.lms.subtopic_mastery_service import ASSESSMENT_SOURCES

    db = get_db()
    evidence = (
        db.query(SubtopicEvidence)
        .filter(
            SubtopicEvidence.student_id.in_(student_ids),
            SubtopicEvidence.source_type.in_(ASSESSMENT_SOURCES),
        )
        .order_by(SubtopicEvidence.created_at.asc(), SubtopicEvidence.id.asc())
        .all()
    )
    qids = {int(e.question_ref) for e in evidence if str(e.question_ref).isdigit()}
    gp_rows = (
        db.query(GuidedPracticeAttempt, TargetBankItem.question_id)
        .join(TargetBankItem, TargetBankItem.id == GuidedPracticeAttempt.bank_item_id)
        .filter(GuidedPracticeAttempt.student_id.in_(student_ids), GuidedPracticeAttempt.status == "answered")
        .all()
    )
    qids |= {qid for _, qid in gp_rows}
    attrs = {
        q.id: (qa.cognitive_level_of(q), qa.known_difficulty(q))
        for q in (db.query(Question).filter(Question.id.in_(qids)).all() if qids else [])
    }
    sub_domain = {e.subtopic_id: e.domain_topic_id for e in evidence if e.domain_topic_id}

    def new_acc():
        return {"correct": 0, "total": 0, "students": {}, "gp_correct": 0, "gp_total": 0}

    # scope key ("s", id) / ("d", id) -> dimension -> level -> accumulator
    data: dict = {}
    unlabelled: dict = {}

    def bucket(scope, dim, level):
        return data.setdefault(scope, {"cognitive": {}, "difficulty": {}})[dim].setdefault(level, new_acc())

    for e in evidence:
        if not str(e.question_ref).isdigit():
            continue
        cog, diff = attrs.get(int(e.question_ref), (None, None))
        domain_id = e.domain_topic_id or sub_domain.get(e.subtopic_id)
        for scope in (("s", e.subtopic_id), ("d", domain_id)):
            if scope[1] is None:
                continue
            un = unlabelled.setdefault(scope, {"cognitive": 0, "difficulty": 0})
            for dim, level in (("cognitive", cog), ("difficulty", diff)):
                if not level:
                    un[dim] += 1
                    continue
                a = bucket(scope, dim, level)
                a["total"] += 1
                a["correct"] += 1 if e.is_correct else 0
                user = users.get(e.student_id)
                st = a["students"].setdefault(
                    e.student_id,
                    {"username": user.username if user else f"Student #{e.student_id}", "attempts": {},
                     "baseline": None, "latest": None},
                )
                key = (e.source_type, e.source_id)
                pair = st["attempts"].setdefault(key, [0, 0])
                pair[0] += 1 if e.is_correct else 0
                pair[1] += 1
                if e.source_type == "diagnostic" and st["baseline"] is None:
                    st["baseline"] = key
                st["latest"] = key  # evidence is ordered oldest-first

    for att, qid in gp_rows:
        cog, diff = attrs.get(qid, (None, None))
        domain_id = sub_domain.get(att.subtopic_id)
        for scope in (("s", att.subtopic_id), ("d", domain_id)):
            if scope[1] is None:
                continue
            for dim, level in (("cognitive", cog), ("difficulty", diff)):
                if level:
                    a = bucket(scope, dim, level)
                    a["gp_total"] += 1
                    a["gp_correct"] += 1 if att.is_correct else 0

    out = {}
    for scope, dims in data.items():
        out[scope] = {
            "cognitive_levels": _level_rows(dims["cognitive"], qa.COGNITIVE_LEVELS),
            "difficulty_levels": _level_rows(dims["difficulty"], qa.DIFFICULTY_LEVELS),
            "unlabelled": unlabelled.get(scope, {"cognitive": 0, "difficulty": 0}),
        }
    return out


def func_count(column):
    from sqlalchemy import func

    return func.count(column)
