"""
REST API for Phase 3 student learning (question bank, events, study artifacts).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any, Dict

from flask import Blueprint, jsonify, request, session

from app.models import UserModel
from app.models.database_models import Lesson as DBLesson
from app.models.phase3_models import (
    AITeachingAdaptation,
    ClassPositiveBenchmark,
    PrepBookTopicAnalysis,
    StudentContentHighlight,
    StudentDiagnosticProfile,
    StudentExamTarget,
    StudentFlashcard,
    StudentLearningPreferences,
    StudentLectureProgress,
    StudentLectureRating,
    StudentOwnedUpload,
    StudentPlanAdherence,
    StudentStudyPlan,
    LectureTeacherReview,
    MiniLectureTarget,
)
from app.services.phase3 import group_study_service, question_bank_service
from app.services.phase3.access import student_can_access_lesson, teacher_owns_lesson
from app.services.phase3.calendar_sync_service import sync_user_calendars
from app.services.phase3.class_benchmark_service import merge_benchmark_or_compute
from app.services.phase3.conversational_study_plan import build_conversational_plan
from app.services.phase3.learning_event_service import emit_learning_event, record_client_events_batch
from app.services.phase3.prep_book_topic_service import extract_topics_from_prep_text
from app.services.phase3.realworld_snippet_service import get_or_create_snippet_payload
from app.services.phase3.student_upload_ocr import run_ocr_for_upload_id
from app.services.phase3.study_plan_service import build_plan_skeleton, plan_to_json
from app.services.phase3.teacher_insights_service import list_student_questions_for_teacher
from app.config import Config
from app.utils.auth import login_required
from app.utils.db import get_db
from app.utils.decorators import student_required, teacher_required

phase3_api_bp = Blueprint("phase3_api", __name__, url_prefix="/api/phase3")


def _uid() -> int:
    return int(session["user_id"])


def _json_error(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def _parse_iso_dt(s: str) -> datetime:
    """Parse API datetime strings (naive local / ISO without timezone)."""
    t = (s or "").strip().replace("Z", "")
    if "T" in t:
        if len(t) == 16:
            return datetime.fromisoformat(t + ":00")
        if len(t) >= 19:
            return datetime.fromisoformat(t[:19])
        return datetime.fromisoformat(t)
    if len(t) == 10 and t[4] == "-":
        return datetime.strptime(t, "%Y-%m-%d")
    return datetime.fromisoformat(t)


# --- Question bank (admin / teacher create; students read) ---
@phase3_api_bp.route("/question-bank/items", methods=["GET"])
@login_required
def qb_list():
    db = get_db()
    topic_id = request.args.get("syllabus_topic_id", type=int)
    items = question_bank_service.list_items(db, syllabus_topic_id=topic_id, active_only=True)
    return jsonify({"items": items})


@phase3_api_bp.route("/question-bank/items", methods=["POST"])
@login_required
def qb_create():
    """Teachers and admins create bank items."""
    user = UserModel(_uid())
    if not (user.is_teacher() or user.is_admin()):
        return _json_error("Forbidden", 403)
    data = request.get_json(silent=True) or {}
    stem = (data.get("stem") or "").strip()
    if not stem:
        return _json_error("stem required")
    diff = int(data.get("difficulty") or 3)
    bloom = (data.get("bloom_level") or "understand").strip().lower()
    db = get_db()
    row = question_bank_service.create_item(
        db,
        stem=stem,
        difficulty=max(1, min(5, diff)),
        bloom_level=bloom,
        syllabus_topic_id=data.get("syllabus_topic_id"),
        tags=data.get("tags"),
        source=data.get("source"),
        explanation=data.get("explanation"),
        metadata=data.get("metadata"),
        created_by_user_id=_uid(),
    )
    return jsonify(question_bank_service.item_to_dict(row)), 201


@phase3_api_bp.route("/question-bank/items/<int:item_id>", methods=["PATCH"])
@login_required
def qb_patch(item_id: int):
    user = UserModel(_uid())
    if not (user.is_teacher() or user.is_admin()):
        return _json_error("Forbidden", 403)
    data = request.get_json(silent=True) or {}
    allowed = {k: data[k] for k in ("stem", "difficulty", "bloom_level", "syllabus_topic_id", "tags", "source", "explanation", "metadata", "is_active") if k in data}
    db = get_db()
    row = question_bank_service.update_item(db, item_id=item_id, **allowed)
    if not row:
        return _json_error("Not found", 404)
    return jsonify(question_bank_service.item_to_dict(row))


@phase3_api_bp.route("/question-bank/items/<int:item_id>", methods=["DELETE"])
@login_required
def qb_delete(item_id: int):
    user = UserModel(_uid())
    if not (user.is_teacher() or user.is_admin()):
        return _json_error("Forbidden", 403)
    db = get_db()
    ok = question_bank_service.soft_delete(db, item_id=item_id)
    if not ok:
        return _json_error("Not found", 404)
    return jsonify({"ok": True})


@phase3_api_bp.route("/question-bank/items/bulk", methods=["POST"])
@login_required
def qb_bulk_csv():
    """Bulk upload CSV (stem, difficulty, bloom_level, syllabus_topic_id, tags)."""
    user = UserModel(_uid())
    if not (user.is_teacher() or user.is_admin()):
        return _json_error("Forbidden", 403)
    raw = ""
    if request.data:
        raw = request.data.decode("utf-8", errors="replace")
    if not (raw or "").strip():
        body = request.get_json(silent=True) or {}
        raw = str(body.get("csv") or "")
    db = get_db()
    res = question_bank_service.bulk_create_from_csv(
        db, csv_text=raw, created_by_user_id=_uid()
    )
    return jsonify(res), 201


# --- Learning events (batch from client) ---
@phase3_api_bp.route("/events/batch", methods=["POST"])
@student_required
def events_batch():
    db = get_db()
    data = request.get_json(silent=True) or {}
    events = data.get("events") or []
    n = record_client_events_batch(db, student_user_id=_uid(), events=events if isinstance(events, list) else [])
    return jsonify({"recorded": n})


# --- Highlights & flashcards ---
@phase3_api_bp.route("/highlights", methods=["GET"])
@student_required
def highlights_list():
    lesson_id = request.args.get("lesson_id", type=int)
    db = get_db()
    q = db.query(StudentContentHighlight).filter(StudentContentHighlight.student_user_id == _uid())
    if lesson_id:
        q = q.filter(StudentContentHighlight.lesson_id == lesson_id)
    rows = q.order_by(StudentContentHighlight.created_at.desc()).limit(200).all()
    out = []
    for r in rows:
        aj = r.anchor_json or "{}"
        try:
            anchor_parsed = json.loads(aj)
        except Exception:
            anchor_parsed = {"raw": aj}
        out.append(
            {
                "id": r.id,
                "lesson_id": r.lesson_id,
                "mode": r.mode,
                "anchor_json": anchor_parsed,
                "excerpt": r.excerpt,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return jsonify({"items": out})


@phase3_api_bp.route("/highlights", methods=["POST"])
@student_required
def highlights_post():
    db = get_db()
    data = request.get_json(silent=True) or {}
    lesson_id = data.get("lesson_id")
    if lesson_id and not student_can_access_lesson(
        db, student_user_id=_uid(), lesson_id=int(lesson_id), user_role=session.get("role")
    ):
        return _json_error("Cannot access lesson", 403)
    mode = data.get("mode") or "lecture"
    anchor = data.get("anchor") or {}
    excerpt = (data.get("excerpt") or "").strip()
    if not excerpt:
        return _json_error("excerpt required")
    row = StudentContentHighlight(
        student_user_id=_uid(),
        lesson_id=lesson_id,
        mode=mode if mode in ("lecture", "self_study") else "lecture",
        anchor_json=json.dumps(anchor, default=str),
        excerpt=excerpt[:8000],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    emit_learning_event(
        db,
        event_type="student.highlight.created",
        payload={"highlight_id": row.id, "lesson_id": lesson_id},
        student_user_id=_uid(),
        lesson_id=lesson_id,
        session_key=data.get("session_key"),
        sync_only=True,
    )
    return jsonify({"id": row.id}), 201


@phase3_api_bp.route("/flashcards", methods=["GET"])
@student_required
def flashcards_list():
    db = get_db()
    rows = (
        db.query(StudentFlashcard)
        .filter(StudentFlashcard.student_user_id == _uid())
        .order_by(StudentFlashcard.updated_at.desc())
        .limit(300)
        .all()
    )
    items = []
    for r in rows:
        srs = {}
        if r.srs_json:
            try:
                srs = json.loads(r.srs_json)
            except Exception:
                srs = {}
        items.append(
            {
                "id": r.id,
                "front": r.front,
                "back": r.back,
                "highlight_id": r.highlight_id,
                "srs": srs,
            }
        )
    return jsonify({"items": items})


@phase3_api_bp.route("/flashcards", methods=["POST"])
@student_required
def flashcards_post():
    db = get_db()
    data = request.get_json(silent=True) or {}
    front = (data.get("front") or "").strip()
    back = (data.get("back") or "").strip()
    if not front or not back:
        return _json_error("front and back required")
    row = StudentFlashcard(
        student_user_id=_uid(),
        front=front[:12000],
        back=back[:12000],
        highlight_id=data.get("highlight_id"),
        srs_json=json.dumps(data.get("srs") or {"ease": 2.5, "interval": 0, "reps": 0}),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return jsonify({"id": row.id}), 201


@phase3_api_bp.route("/flashcards/<int:fc_id>/review", methods=["POST"])
@student_required
def flashcards_review(fc_id: int):
    """SRS update hook — simplified SM-2-ish."""
    db = get_db()
    row = (
        db.query(StudentFlashcard)
        .filter(StudentFlashcard.id == fc_id, StudentFlashcard.student_user_id == _uid())
        .first()
    )
    if not row:
        return _json_error("Not found", 404)
    data = request.get_json(silent=True) or {}
    quality = int(data.get("quality") or 3)  # 0-5
    srs = {}
    if row.srs_json:
        try:
            srs = json.loads(row.srs_json)
        except Exception:
            srs = {}
    reps = int(srs.get("reps") or 0) + 1
    interval = int(srs.get("interval") or 0)
    if quality < 3:
        interval = 0
    else:
        interval = max(1, int(interval * 1.6) + 1)
    srs.update({"reps": reps, "interval": interval, "last_quality": quality})
    row.srs_json = json.dumps(srs)
    db.commit()
    return jsonify({"srs": srs})


# --- Progress (lecture vs self-study) ---
@phase3_api_bp.route("/progress", methods=["POST"])
@student_required
def progress_post():
    db = get_db()
    data = request.get_json(silent=True) or {}
    lesson_id = data.get("lesson_id")
    if not lesson_id:
        return _json_error("lesson_id required")
    if not student_can_access_lesson(
        db, student_user_id=_uid(), lesson_id=int(lesson_id), user_role=session.get("role")
    ):
        return _json_error("Forbidden", 403)
    mode = data.get("mode") or "lecture"
    row = (
        db.query(StudentLectureProgress)
        .filter(
            StudentLectureProgress.student_user_id == _uid(),
            StudentLectureProgress.lesson_id == int(lesson_id),
            StudentLectureProgress.mode == mode,
        )
        .first()
    )
    if not row:
        row = StudentLectureProgress(
            student_user_id=_uid(),
            lesson_id=int(lesson_id),
            mode=mode,
        )
        db.add(row)
    row.position_json = json.dumps(data.get("position") or {}, default=str)
    if data.get("percent_complete") is not None:
        row.percent_complete = data.get("percent_complete")
    db.commit()
    return jsonify({"ok": True})


@phase3_api_bp.route("/preferences", methods=["GET"])
@student_required
def prefs_get():
    db = get_db()
    row = db.query(StudentLearningPreferences).filter(StudentLearningPreferences.student_user_id == _uid()).first()
    if not row:
        return jsonify(
            {
                "allow_teacher_view_self_study": True,
                "reminder_channels": {"push": True, "email": False, "sms": False},
                "daily_goal_minutes": None,
                "streak_days": 0,
            }
        )
    ch = {}
    if row.reminder_channels_json:
        try:
            ch = json.loads(row.reminder_channels_json)
        except Exception:
            ch = {}
    return jsonify(
        {
            "allow_teacher_view_self_study": row.allow_teacher_view_self_study,
            "reminder_channels": ch,
            "daily_goal_minutes": row.daily_goal_minutes,
            "streak_days": row.streak_days,
        }
    )


@phase3_api_bp.route("/preferences", methods=["PUT"])
@student_required
def prefs_put():
    db = get_db()
    data = request.get_json(silent=True) or {}
    row = db.query(StudentLearningPreferences).filter(StudentLearningPreferences.student_user_id == _uid()).first()
    if not row:
        row = StudentLearningPreferences(student_user_id=_uid())
        db.add(row)
    if "allow_teacher_view_self_study" in data:
        row.allow_teacher_view_self_study = bool(data["allow_teacher_view_self_study"])
    if "reminder_channels" in data:
        row.reminder_channels_json = json.dumps(data["reminder_channels"], default=str)
    if "daily_goal_minutes" in data:
        row.daily_goal_minutes = data["daily_goal_minutes"]
    db.commit()
    return jsonify({"ok": True})


@phase3_api_bp.route("/exam-target", methods=["POST"])
@student_required
def exam_target_post():
    db = get_db()
    data = request.get_json(silent=True) or {}
    raw_date = data.get("exam_date")
    if not raw_date:
        return _json_error("exam_date required")
    try:
        ed = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
    except Exception:
        return _json_error("invalid exam_date")
    row = StudentExamTarget(
        student_user_id=_uid(),
        exam_type_id=data.get("exam_type_id"),
        exam_date=ed,
        label=(data.get("label") or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return jsonify({"id": row.id, "exam_date": row.exam_date.isoformat()}), 201


@phase3_api_bp.route("/exam-targets", methods=["GET"])
@student_required
def exam_targets_list():
    db = get_db()
    rows = (
        db.query(StudentExamTarget)
        .filter(StudentExamTarget.student_user_id == _uid())
        .order_by(StudentExamTarget.exam_date.asc())
        .limit(20)
        .all()
    )
    items = [
        {
            "id": r.id,
            "exam_type_id": r.exam_type_id,
            "exam_date": r.exam_date.isoformat() if r.exam_date else None,
            "label": r.label,
        }
        for r in rows
    ]
    return jsonify({"items": items})


@phase3_api_bp.route("/study-plans/generate", methods=["POST"])
@student_required
def study_plan_generate():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        plan = build_plan_skeleton(
            db,
            exam_type_id=int(data["exam_type_id"]),
            grade=str(data["grade"]),
            platform_subject_id=int(data["platform_subject_id"]),
            horizon_days=int(data.get("horizon_days") or 30),
            hours_per_day=float(data.get("hours_per_day") or 2),
            weak_topic_ids=data.get("weak_topic_ids"),
        )
    except (KeyError, TypeError, ValueError) as e:
        return _json_error(str(e))
    title = (data.get("title") or "My study plan").strip()
    row = StudentStudyPlan(
        student_user_id=_uid(),
        title=title[:500],
        plan_json=plan_to_json(plan),
        horizon_days=int(data.get("horizon_days") or 30),
        exam_target_id=data.get("exam_target_id"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return jsonify({"id": row.id, "plan": plan}), 201


@phase3_api_bp.route("/ratings", methods=["POST"])
@student_required
def rating_post():
    db = get_db()
    data = request.get_json(silent=True) or {}
    lesson_id = data.get("lesson_id")
    stars = int(data.get("stars") or 0)
    if not lesson_id or stars < 1 or stars > 5:
        return _json_error("lesson_id and stars 1-5 required")
    if not student_can_access_lesson(
        db, student_user_id=_uid(), lesson_id=int(lesson_id), user_role=session.get("role")
    ):
        return _json_error("Forbidden", 403)
    thr = int(data.get("threshold_seconds_required") or 120)
    existing = (
        db.query(StudentLectureRating)
        .filter(
            StudentLectureRating.student_user_id == _uid(),
            StudentLectureRating.lesson_id == int(lesson_id),
        )
        .first()
    )
    if existing:
        existing.stars = stars
        existing.comment = (data.get("comment") or "").strip() or None
        existing.engagement_seconds = data.get("engagement_seconds")
        existing.threshold_seconds_required = thr
    else:
        db.add(
            StudentLectureRating(
                student_user_id=_uid(),
                lesson_id=int(lesson_id),
                stars=stars,
                comment=(data.get("comment") or "").strip() or None,
                engagement_seconds=data.get("engagement_seconds"),
                threshold_seconds_required=thr,
            )
        )
    db.commit()
    return jsonify({"ok": True}), 201


@phase3_api_bp.route("/diagnostic", methods=["POST"])
@student_required
def diagnostic_post():
    db = get_db()
    data = request.get_json(silent=True) or {}
    row = db.query(StudentDiagnosticProfile).filter(StudentDiagnosticProfile.student_user_id == _uid()).first()
    if not row:
        row = StudentDiagnosticProfile(student_user_id=_uid())
        db.add(row)

    prev: Dict[str, Any] = {}
    if row.baseline_json:
        try:
            loaded = json.loads(row.baseline_json)
            prev = loaded if isinstance(loaded, dict) else {"legacy_baseline": loaded}
        except Exception:
            prev = {}

    incoming = data.get("baseline")
    if incoming is None:
        incoming = data.get("baseline_json")
    if isinstance(incoming, str):
        try:
            incoming = json.loads(incoming)
        except Exception:
            incoming = None

    if isinstance(incoming, dict):
        prev.update(incoming)
    elif incoming not in (None, [], {}):
        prev["baseline_value"] = incoming

    if data.get("increment_session"):
        prev["diagnostic_sessions"] = int(prev.get("diagnostic_sessions") or 0) + 1

    acc = data.get("last_session_accuracy")
    if acc is not None:
        try:
            acc_f = float(acc)
            n = max(1, int(prev.get("diagnostic_sessions") or 1))
            old = float(prev.get("rolling_accuracy") or acc_f)
            prev["rolling_accuracy"] = round((old * (n - 1) + acc_f) / n, 4)
        except (TypeError, ValueError):
            pass

    explicit_skip = data.get("skipped")
    if explicit_skip is True:
        skipped = True
    elif explicit_skip is False:
        skipped = False
    else:
        skipped = not prev

    row.skipped = bool(skipped)
    row.status = "skipped" if skipped else "completed"
    row.baseline_json = json.dumps(prev, default=str)
    db.commit()
    return jsonify({"ok": True, "baseline": prev})


@phase3_api_bp.route("/diagnostic/profile", methods=["GET"])
@student_required
def diagnostic_profile_get():
    db = get_db()
    row = (
        db.query(StudentDiagnosticProfile)
        .filter(StudentDiagnosticProfile.student_user_id == _uid())
        .first()
    )
    if not row:
        return jsonify({"status": "none", "baseline": {}})
    base: Dict[str, Any] = {}
    if row.baseline_json:
        try:
            base = json.loads(row.baseline_json)
        except Exception:
            base = {}
    return jsonify(
        {
            "status": row.status,
            "skipped": row.skipped,
            "baseline": base,
        }
    )


@phase3_api_bp.route("/adaptation", methods=["POST"])
@student_required
def adaptation_post():
    db = get_db()
    data = request.get_json(silent=True) or {}
    row = AITeachingAdaptation(
        student_user_id=_uid(),
        lesson_id=data.get("lesson_id"),
        session_key=data.get("session_key"),
        adaptation_type=str(data.get("adaptation_type") or "strategy_shift"),
        reason=str(data.get("reason") or "")[:8000],
        meta_json=json.dumps(data.get("meta") or {}, default=str),
    )
    db.add(row)
    db.commit()
    return jsonify({"id": row.id}), 201


@phase3_api_bp.route("/adherence", methods=["POST"])
@student_required
def adherence_post():
    db = get_db()
    data = request.get_json(silent=True) or {}
    day_s = data.get("day") or date.today().isoformat()
    try:
        d = datetime.strptime(day_s[:10], "%Y-%m-%d").date()
    except Exception:
        return _json_error("invalid day")
    row = (
        db.query(StudentPlanAdherence)
        .filter(StudentPlanAdherence.student_user_id == _uid(), StudentPlanAdherence.day == d)
        .first()
    )
    if not row:
        row = StudentPlanAdherence(student_user_id=_uid(), day=d)
        db.add(row)
    row.planned_minutes = data.get("planned_minutes")
    row.actual_minutes = data.get("actual_minutes")
    row.missed = bool(data.get("missed"))
    db.commit()
    return jsonify({"ok": True})


@phase3_api_bp.route("/adherence/history", methods=["GET"])
@student_required
def adherence_history():
    db = get_db()
    rows = (
        db.query(StudentPlanAdherence)
        .filter(StudentPlanAdherence.student_user_id == _uid())
        .order_by(StudentPlanAdherence.day.desc())
        .limit(60)
        .all()
    )
    items = [
        {
            "day": r.day.isoformat() if r.day else None,
            "planned_minutes": r.planned_minutes,
            "actual_minutes": r.actual_minutes,
            "missed": r.missed,
        }
        for r in rows
    ]
    return jsonify({"items": items})


@phase3_api_bp.route("/dashboard-summary", methods=["GET"])
@student_required
def dashboard_summary():
    db = get_db()
    uid = _uid()
    prefs = db.query(StudentLearningPreferences).filter(StudentLearningPreferences.student_user_id == uid).first()
    exams = db.query(StudentExamTarget).filter(StudentExamTarget.student_user_id == uid).order_by(StudentExamTarget.exam_date.asc()).limit(3).all()
    plans = db.query(StudentStudyPlan).filter(StudentStudyPlan.student_user_id == uid).order_by(StudentStudyPlan.updated_at.desc()).limit(1).all()
    fc_count = db.query(StudentFlashcard).filter(StudentFlashcard.student_user_id == uid).count()

    countdown = None
    if exams:
        delta = (exams[0].exam_date - date.today()).days
        countdown = {"exam_target_id": exams[0].id, "days": delta, "label": exams[0].label}

    plan_preview = None
    if plans:
        try:
            plan_preview = json.loads(plans[0].plan_json)
        except Exception:
            plan_preview = {}

    return jsonify(
        {
            "streak_days": prefs.streak_days if prefs else 0,
            "daily_goal_minutes": prefs.daily_goal_minutes if prefs else None,
            "exam_countdown": countdown,
            "latest_plan": plan_preview,
            "flashcard_count": fc_count,
        }
    )


@phase3_api_bp.route("/class-comparison", methods=["GET"])
@student_required
def class_comparison():
    """Positive-only anonymised metrics — no rankings."""
    db = get_db()
    section_id = request.args.get("class_section_id", type=int)
    if not section_id:
        return _json_error("class_section_id required")
    from app.services.school.access import can_student_access_class_section

    if not can_student_access_class_section(db, _uid(), section_id):
        return _json_error("Forbidden", 403)
    bench = (
        db.query(ClassPositiveBenchmark)
        .filter(ClassPositiveBenchmark.class_section_id == section_id)
        .order_by(ClassPositiveBenchmark.period_end.desc())
        .first()
    )
    stored = bench.metrics_json if bench else None
    metrics = merge_benchmark_or_compute(
        db, class_section_id=section_id, stored_metrics_json=stored
    )
    return jsonify({"positive_framing": True, "metrics": metrics})


@phase3_api_bp.route("/teacher/review", methods=["POST"])
@teacher_required
def teacher_review_save():
    db = get_db()
    data = request.get_json(silent=True) or {}
    lesson_id = data.get("lesson_id")
    if not lesson_id:
        return _json_error("lesson_id required")
    if not teacher_owns_lesson(db, teacher_user_id=_uid(), lesson_id=int(lesson_id)):
        return _json_error("Forbidden", 403)
    row = LectureTeacherReview(
        lesson_id=int(lesson_id),
        teacher_user_id=_uid(),
        ai_summary=data.get("ai_summary"),
        reflection_prompt=data.get("reflection_prompt"),
        reflection_response=data.get("reflection_response"),
        payload_json=json.dumps(data.get("payload") or {}, default=str),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return jsonify({"id": row.id}), 201


@phase3_api_bp.route("/teacher/mini-lecture/targets", methods=["POST"])
@teacher_required
def mini_lecture_targets():
    db = get_db()
    data = request.get_json(silent=True) or {}
    mini_lesson_id = data.get("mini_lesson_id")
    student_ids = data.get("student_ids") or []
    review_id = data.get("source_review_id")
    if not mini_lesson_id or not isinstance(student_ids, list):
        return _json_error("mini_lesson_id and student_ids required")
    if not teacher_owns_lesson(db, teacher_user_id=_uid(), lesson_id=int(mini_lesson_id)):
        return _json_error("Forbidden", 403)
    for sid in student_ids:
        exists = (
            db.query(MiniLectureTarget)
            .filter(
                MiniLectureTarget.mini_lesson_id == int(mini_lesson_id),
                MiniLectureTarget.student_user_id == int(sid),
            )
            .first()
        )
        if exists:
            continue
        db.add(
            MiniLectureTarget(
                mini_lesson_id=int(mini_lesson_id),
                student_user_id=int(sid),
                source_review_id=review_id,
            )
        )
    db.commit()
    return jsonify({"ok": True})


@phase3_api_bp.route("/uploads", methods=["GET", "POST"])
@student_required
def upload_material():
    """List uploads (GET) or store file under instance path (POST); OCR hooks marked pending."""
    db = get_db()
    if request.method == "GET":
        rows = (
            db.query(StudentOwnedUpload)
            .filter(StudentOwnedUpload.student_user_id == _uid())
            .order_by(StudentOwnedUpload.created_at.desc())
            .limit(100)
            .all()
        )
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "category": r.category,
                    "original_name": r.original_name,
                    "ocr_status": r.ocr_status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return jsonify({"items": items})

    from werkzeug.utils import secure_filename
    from flask import current_app

    f = request.files.get("file")
    if not f or not f.filename:
        return _json_error("file required")
    cat = (request.form.get("category") or "content_book").strip()
    if cat not in ("prep_book", "content_book"):
        return _json_error("category must be prep_book or content_book")
    dest_dir = os.path.join(current_app.instance_path, "student_uploads", str(_uid()))
    os.makedirs(dest_dir, exist_ok=True)
    fname = secure_filename(f.filename)
    path = os.path.join(dest_dir, fname)
    f.save(path)
    db = get_db()
    row = StudentOwnedUpload(
        student_user_id=_uid(),
        category=cat,
        storage_path=path,
        mime_type=f.mimetype,
        original_name=fname,
        ocr_status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    emit_learning_event(
        db,
        event_type="student.upload.created",
        payload={"upload_id": row.id, "category": cat},
        student_user_id=_uid(),
        sync_only=True,
    )
    try:
        from app.tasks.phase3_tasks import ocr_student_upload_task

        ocr_student_upload_task.delay(row.id)
    except Exception:
        if os.getenv("PHASE3_OCR_SYNC_FALLBACK", "true").lower() in ("1", "true", "yes"):
            run_ocr_for_upload_id(db, row.id)
            db.refresh(row)
    return jsonify({"id": row.id, "ocr_status": row.ocr_status}), 201


@phase3_api_bp.route("/prep-books/<int:upload_id>/analyze-topics", methods=["POST"])
@student_required
def analyze_prep_topics(upload_id: int):
    """Extract topics from prep-book OCR text (LLM optional)."""
    db = get_db()
    row = (
        db.query(StudentOwnedUpload)
        .filter(StudentOwnedUpload.id == upload_id, StudentOwnedUpload.student_user_id == _uid())
        .first()
    )
    if not row or row.category != "prep_book":
        return _json_error("Not found", 404)

    if row.ocr_status != "completed" or not (row.ocr_extracted_text or "").strip():
        run_ocr_for_upload_id(db, upload_id)
        db.refresh(row)

    text = (row.ocr_extracted_text or "").strip()
    groq_key = (session.get("groq_api_key") or Config.GROQ_API_KEY or "").strip() or None
    payload = extract_topics_from_prep_text(text, groq_api_key=groq_key)
    payload["upload_id"] = upload_id
    payload["ocr_status"] = row.ocr_status

    notes: Dict[str, Any] = {}
    if row.ai_notes:
        try:
            notes = json.loads(row.ai_notes)
            if not isinstance(notes, dict):
                notes = {"prior_note": str(row.ai_notes)[:2000]}
        except Exception:
            notes = {"prior_note": str(row.ai_notes)[:2000]}
    notes["topic_analysis"] = {"method": payload.get("method")}
    row.ai_notes = json.dumps(notes, default=str)[:8000]
    db.commit()
    return jsonify(payload)


@phase3_api_bp.route("/calendar/export.ics", methods=["GET"])
@student_required
def export_ics():
    """Minimal single-calendar ICS from latest study plan."""
    db = get_db()
    plan = (
        db.query(StudentStudyPlan)
        .filter(StudentStudyPlan.student_user_id == _uid())
        .order_by(StudentStudyPlan.updated_at.desc())
        .first()
    )
    if not plan:
        return _json_error("No study plan", 404)
    try:
        blob = json.loads(plan.plan_json)
    except Exception:
        blob = {}
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//IqbalAI//Phase3//EN"]
    # Pull daily entries from sections
    for sec in blob.get("sections") or []:
        for day in sec.get("days") or []:
            ds = day.get("date")
            title = day.get("focus_title") or "Study block"
            lines.append("BEGIN:VEVENT")
            lines.append(f"DTSTART;VALUE=DATE:{ds.replace('-', '')}")
            lines.append(f"SUMMARY:{title}")
            lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    from flask import Response

    return Response("\r\n".join(lines), mimetype="text/calendar")


@phase3_api_bp.route("/calendar/sync", methods=["POST"])
@student_required
def calendar_sync_push():
    """Push latest study plan blocks to connected Google / Apple calendars."""
    db = get_db()
    try:
        result = sync_user_calendars(db, student_user_id=_uid())
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    except Exception as exc:
        return _json_error(str(exc), 500)


@phase3_api_bp.route("/realworld/<int:syllabus_topic_id>", methods=["GET"])
@login_required
def realworld_get(syllabus_topic_id: int):
    db = get_db()
    groq_key = (session.get("groq_api_key") or Config.GROQ_API_KEY or "").strip() or None
    payload = get_or_create_snippet_payload(db, syllabus_topic_id=syllabus_topic_id, groq_api_key=groq_key)
    return jsonify(payload)


@phase3_api_bp.route("/progress", methods=["GET"])
@student_required
def progress_list():
    db = get_db()
    rows = (
        db.query(StudentLectureProgress)
        .filter(StudentLectureProgress.student_user_id == _uid())
        .order_by(StudentLectureProgress.updated_at.desc())
        .limit(100)
        .all()
    )
    items = []
    for r in rows:
        pos = {}
        if r.position_json:
            try:
                pos = json.loads(r.position_json)
            except Exception:
                pos = {}
        items.append(
            {
                "lesson_id": r.lesson_id,
                "mode": r.mode,
                "position": pos,
                "percent_complete": float(r.percent_complete) if r.percent_complete is not None else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )
    return jsonify({"items": items})


@phase3_api_bp.route("/teacher/lessons/<int:lesson_id>/student-questions", methods=["GET"])
@teacher_required
def teacher_lesson_student_questions(lesson_id: int):
    db = get_db()
    items = list_student_questions_for_teacher(db, lesson_id=lesson_id, teacher_user_id=_uid())
    return jsonify({"items": items})


@phase3_api_bp.route("/study-plans/conversational", methods=["POST"])
@student_required
def study_plan_conversational():
    db = get_db()
    data = request.get_json(silent=True) or {}
    api_key = (
        (data.get("groq_api_key") or "").strip()
        or session.get("groq_api_key")
        or Config.GROQ_API_KEY
        or ""
    )
    try:
        plan = build_conversational_plan(
            db,
            student_transcript=str(data.get("transcript") or ""),
            exam_type_id=int(data["exam_type_id"]),
            grade=str(data["grade"]),
            platform_subject_id=int(data["platform_subject_id"]),
            horizon_days=int(data.get("horizon_days") or 30),
            hours_per_day=float(data.get("hours_per_day") or 2),
            weak_topic_ids=data.get("weak_topic_ids"),
            groq_api_key=api_key or None,
        )
    except (KeyError, TypeError, ValueError) as e:
        return _json_error(str(e))
    title = (data.get("title") or "My conversational plan").strip()
    row = StudentStudyPlan(
        student_user_id=_uid(),
        title=title[:500],
        plan_json=plan_to_json(plan),
        horizon_days=int(data.get("horizon_days") or 30),
        exam_target_id=data.get("exam_target_id"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return jsonify({"id": row.id, "plan": plan}), 201


@phase3_api_bp.route("/flashcards/from-highlight", methods=["POST"])
@student_required
def flashcard_from_highlight():
    db = get_db()
    data = request.get_json(silent=True) or {}
    hid = data.get("highlight_id")
    front = (data.get("front") or "").strip()
    back = (data.get("back") or "").strip()
    if hid:
        hl = (
            db.query(StudentContentHighlight)
            .filter(StudentContentHighlight.id == int(hid), StudentContentHighlight.student_user_id == _uid())
            .first()
        )
        if hl:
            front = front or hl.excerpt[:500]
    if not front or not back:
        return _json_error("front and back (or highlight_id + back) required")
    row = StudentFlashcard(
        student_user_id=_uid(),
        front=front[:12000],
        back=back[:12000],
        highlight_id=int(hid) if hid else None,
        srs_json=json.dumps({"ease": 2.5, "interval": 0, "reps": 0}),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return jsonify({"id": row.id}), 201


@phase3_api_bp.route("/diagnostic/session", methods=["POST"])
@student_required
def diagnostic_session():
    """Return next question from bank with simple adaptive difficulty (1–5)."""
    db = get_db()
    data = request.get_json(silent=True) or {}
    idx = int(data.get("index") or 0)
    diff = max(1, min(5, int(data.get("current_difficulty") or 3)))

    last_correct = data.get("last_answer_correct")
    if last_correct is True:
        diff = min(5, diff + 1)
    elif last_correct is False:
        diff = max(1, diff - 1)

    pool = question_bank_service.list_items_near_difficulty(db, center=diff, spread=1, limit=80)
    if not pool:
        pool = question_bank_service.list_items(db, active_only=True, limit=80)
    if not pool:
        return jsonify({"done": True, "message": "Seed question bank items first"})

    pick = pool[idx % len(pool)]
    return jsonify(
        {
            "index": idx,
            "question": pick,
            "current_difficulty": diff,
            "suggested_next_difficulty": diff,
            "total_bank": len(pool),
            "adaptive": True,
        }
    )


# --- Group study (teacher schedules; students RSVP) ---


@phase3_api_bp.route("/teacher/group-study/slots", methods=["GET", "POST"])
@teacher_required
def teacher_group_study_slots():
    db = get_db()
    uid = _uid()
    if request.method == "GET":
        return jsonify({"items": group_study_service.list_slots_for_teacher(db, teacher_user_id=uid)})

    data = request.get_json(silent=True) or {}
    try:
        raw_lid = data.get("lesson_id")
        if raw_lid in (None, ""):
            lesson_id = None
        else:
            lesson_id = int(raw_lid)
        starts_at = _parse_iso_dt(str(data.get("starts_at") or ""))
        ends_at = _parse_iso_dt(str(data.get("ends_at") or ""))
        row = group_study_service.create_slot(
            db,
            teacher_user_id=uid,
            lesson_id=lesson_id,
            title=str(data.get("title") or "Group study"),
            starts_at=starts_at,
            ends_at=ends_at,
            max_students=int(data.get("max_students") or 8),
            notes=data.get("notes"),
        )
        return jsonify({"id": row.id}), 201
    except PermissionError as exc:
        return _json_error(str(exc), 403)
    except ValueError as exc:
        return _json_error(str(exc))


@phase3_api_bp.route("/teacher/group-study/slots/<int:slot_id>", methods=["DELETE"])
@teacher_required
def teacher_group_study_cancel(slot_id: int):
    db = get_db()
    ok = group_study_service.cancel_slot_teacher(db, teacher_user_id=_uid(), slot_id=slot_id)
    if not ok:
        return _json_error("Not found", 404)
    return jsonify({"ok": True})


@phase3_api_bp.route("/group-study/slots", methods=["GET"])
@student_required
def student_group_study_list():
    db = get_db()
    return jsonify({"items": group_study_service.list_slots_for_student(db, student_user_id=_uid())})


@phase3_api_bp.route("/group-study/slots/<int:slot_id>/rsvp", methods=["POST", "DELETE"])
@student_required
def student_group_study_rsvp(slot_id: int):
    db = get_db()
    if request.method == "DELETE":
        ok = group_study_service.cancel_rsvp(db, student_user_id=_uid(), slot_id=slot_id)
        if not ok:
            return _json_error("No RSVP", 404)
        return jsonify({"ok": True})
    try:
        group_study_service.rsvp_student(db, student_user_id=_uid(), slot_id=slot_id)
        return jsonify({"ok": True})
    except PermissionError as exc:
        return _json_error(str(exc), 403)
    except ValueError as exc:
        return _json_error(str(exc))


