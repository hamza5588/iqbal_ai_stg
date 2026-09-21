"""Student LMS profile and onboarding state."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from app.models.lms_models import AssessmentAttempt, StudentProfile
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


def clear_diagnostic_completion_for_assessments(assessment_ids: Iterable[int]) -> int:
    """Clear completion flags for students who finished the given diagnostics.

    Used when an admin archives/replaces a diagnostic so the new published
    assessment becomes takeable on the student dashboard.
    """
    ids = [int(x) for x in assessment_ids if x is not None]
    if not ids:
        return 0
    db = get_db()
    profiles = (
        db.query(StudentProfile)
        .filter(StudentProfile.diagnostic_assessment_id.in_(ids))
        .all()
    )
    for profile in profiles:
        profile.diagnostic_completed = False
        profile.diagnostic_completed_at = None
        profile.diagnostic_assessment_id = None
    if profiles:
        db.commit()
    return len(profiles)


def set_current_learning_path(student_id: int, path_id: Optional[int]) -> StudentProfile:
    db = get_db()
    profile = get_or_create_profile(student_id)
    profile.current_learning_path_id = path_id
    db.commit()
    db.refresh(profile)
    return profile


def _completed_active_diagnostic(student_id: int, profile: StudentProfile) -> tuple[bool, Optional[int]]:
    """Whether the student has completed the currently published diagnostic for their grade."""
    from app.services.lms.assessment_service import get_active_platform_diagnostic

    active = get_active_platform_diagnostic(class_service.get_student_grade(student_id))
    if active is None:
        # Nothing published to take — keep historical flag so the gate does not
        # force students into a 404 diagnostic flow.
        return bool(profile.diagnostic_completed), profile.diagnostic_assessment_id

    active_id = int(active.id)
    if (
        profile.diagnostic_completed
        and profile.diagnostic_assessment_id is not None
        and int(profile.diagnostic_assessment_id) == active_id
    ):
        return True, active_id

    db = get_db()
    submitted = (
        db.query(AssessmentAttempt)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.assessment_id == active_id,
            AssessmentAttempt.status == "submitted",
        )
        .first()
    )
    return submitted is not None, active_id


def get_onboarding_status(student_id: int) -> dict:
    profile = get_or_create_profile(student_id)
    classes = class_service.list_student_classes(student_id)
    assignments = assignment_service.list_assignments_for_student(student_id)
    pending = [a for a in assignments if a.get("status") in ("not_started", "in_progress")]

    diagnostic_completed, active_assessment_id = _completed_active_diagnostic(student_id, profile)
    needs_onboarding = not diagnostic_completed
    needs_diagnostic = not diagnostic_completed
    return {
        "diagnostic_completed": diagnostic_completed,
        "diagnostic_assessment_id": (
            active_assessment_id if diagnostic_completed else profile.diagnostic_assessment_id
        ),
        "diagnostic_completed_at": profile.diagnostic_completed_at.isoformat()
        if diagnostic_completed and profile.diagnostic_completed_at
        else None,
        "current_learning_path_id": profile.current_learning_path_id,
        "enrolled_class_count": len(classes),
        "pending_assignment_count": len(pending),
        "needs_onboarding": needs_onboarding,
        "needs_diagnostic": needs_diagnostic,
        "pending_assignments": pending[:10],
        "any_diagnostic_completed": bool(profile.diagnostic_completed),
        "active_diagnostic_assessment_id": active_assessment_id,
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
    # student). Live entries always carry a real mastery_status, so this
    # also removes the "UNKNOWN" badge diagnostic-only entries showed
    # (lmsMasteryBadge() falls back to 'unknown' when status is missing).
    weak_topics = [
        {**m, "topic_name": m.get("topic_name") or f"Topic #{m['topic_id']}"}
        for m in mastery
        if (m.get("score_percent") or 100) < performance_service.PRACTICE_TARGET_THRESHOLD
    ]
    weak_topics.sort(key=lambda m: m.get("score_percent") or 0)
    if not weak_topics:
        weak_topics = _diagnostic_weak_topics(student_id)
    overall_progress = performance_service.get_overall_progress(student_id)
    learning_path = learning_path_service.ensure_learning_path(student_id)

    current_step = None
    if learning_path and learning_path.get("items"):
        for item in learning_path["items"]:
            if item.get("status") != "completed":
                current_step = item
                break

    return {
        "onboarding": onboarding,
        "overall_progress": overall_progress,
        "weak_topics": weak_topics[:5],
        # Full per-topic mastery breakdown (all statuses) - powers the
        # mastery pie chart. weak_topics above stays a filtered/sorted
        # top-5 for the existing list view.
        "mastery": mastery,
        "pending_assignments": onboarding["pending_assignments"],
        "learning_path": learning_path,
        "current_learning_path_step": current_step,
        "classes": [
            {"id": c.id, "name": c.name, "grade_level": c.grade_level}
            for c in class_service.list_student_classes(student_id)
        ],
    }
