"""Student LMS profile and onboarding state."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.models.lms_models import StudentProfile
from app.services.lms import assignment_service, class_service
from app.utils.db import get_db


def get_or_create_profile(student_id: int) -> StudentProfile:
    db = get_db()
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == student_id).first()
    if profile:
        return profile
    profile = StudentProfile(user_id=student_id, diagnostic_completed=False)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def mark_diagnostic_complete(student_id: int, assessment_id: Optional[int] = None) -> StudentProfile:
    db = get_db()
    profile = get_or_create_profile(student_id)
    profile.diagnostic_completed = True
    profile.diagnostic_completed_at = datetime.utcnow()
    if assessment_id is not None:
        profile.diagnostic_assessment_id = assessment_id
    db.commit()
    db.refresh(profile)
    return profile


def set_current_learning_path(student_id: int, path_id: Optional[int]) -> StudentProfile:
    db = get_db()
    profile = get_or_create_profile(student_id)
    profile.current_learning_path_id = path_id
    db.commit()
    db.refresh(profile)
    return profile


def get_onboarding_status(student_id: int) -> dict:
    profile = get_or_create_profile(student_id)
    classes = class_service.list_student_classes(student_id)
    assignments = assignment_service.list_assignments_for_student(student_id)
    pending = [a for a in assignments if a.get("status") in ("not_started", "in_progress")]

    needs_onboarding = not profile.diagnostic_completed
    needs_diagnostic = not profile.diagnostic_completed
    return {
        "diagnostic_completed": profile.diagnostic_completed,
        "diagnostic_assessment_id": profile.diagnostic_assessment_id,
        "diagnostic_completed_at": profile.diagnostic_completed_at.isoformat()
        if profile.diagnostic_completed_at
        else None,
        "current_learning_path_id": profile.current_learning_path_id,
        "enrolled_class_count": len(classes),
        "pending_assignment_count": len(pending),
        "needs_onboarding": needs_onboarding,
        "needs_diagnostic": needs_diagnostic,
        "pending_assignments": pending[:10],
    }


def _diagnostic_weak_topics(student_id: int) -> list:
    from app.services.lms import performance_service

    profile = get_or_create_profile(student_id)
    if not profile.diagnostic_assessment_id:
        return []
    return performance_service.get_diagnostic_weak_topics(
        student_id, assessment_id=profile.diagnostic_assessment_id
    )


def get_student_dashboard(student_id: int) -> dict:
    from app.services.lms import learning_path_service, performance_service, path_generator

    # One-time self-heal for accounts whose mastery predates the
    # AI-grouping fix (their diagnostic and practice topic scores sit on
    # mismatched topic_ids - weak topics never clear, progress inflates).
    performance_service.maybe_rebuild_stale_mastery(student_id)

    onboarding = get_onboarding_status(student_id)
    mastery = performance_service.get_student_mastery(student_id)
    # Prefer live StudentTopicScore over the frozen diagnostic snapshot: a
    # topic the student has since practiced (Learning Chat, quizzes) must
    # drop off here once mastered, not stay stuck showing the diagnostic's
    # original result forever. Falls back to the diagnostic-derived list
    # only when there's no live mastery data at all (e.g. brand-new
    # student).
    #
    # Weak Topics must match the Topic Mastery Breakdown pie "Weak" slice
    # (mastery_status == weak / score < WEAK_THRESHOLD). Do NOT use the
    # practice-until-~100% threshold here — that is for the learning path
    # only — and never silently truncate to 5 while the pie shows all N.
    weak_topics = [
        {**m, "topic_name": m.get("topic_name") or f"Topic #{m['topic_id']}"}
        for m in mastery
        if performance_service.is_weak_mastery(m)
    ]
    weak_topics.sort(key=lambda m: performance_service.score_percent_value(m))
    if not weak_topics and not mastery:
        weak_topics = _diagnostic_weak_topics(student_id)
    overall_progress = performance_service.get_overall_progress(student_id)
    learning_path = learning_path_service.ensure_learning_path(student_id)

    current_step = None
    if learning_path and learning_path.get("items"):
        for item in learning_path["items"]:
            if item.get("status") != "completed":
                current_step = item
                break

    path_completed = int((learning_path or {}).get("completed_count") or 0)
    path_total = int((learning_path or {}).get("total_count") or 0)
    path_percent = (learning_path or {}).get("percent")
    if path_percent is None:
        path_percent = round(100.0 * path_completed / path_total, 1) if path_total else 0.0
    learning_path_progress = {
        "completed": path_completed,
        "total": path_total,
        "percent": path_percent,
    }

    return {
        "onboarding": onboarding,
        "overall_progress": overall_progress,
        "weak_topics": weak_topics,
        # Full per-topic mastery breakdown (all statuses) — powers the
        # mastery pie chart. weak_topics above is the same "Weak" set the
        # pie counts.
        "mastery": mastery,
        "pending_assignments": onboarding["pending_assignments"],
        "learning_path": learning_path,
        "learning_path_progress": learning_path_progress,
        "current_learning_path_step": current_step,
        "classes": [
            {"id": c.id, "name": c.name, "grade_level": c.grade_level}
            for c in class_service.list_student_classes(student_id)
        ],
    }
