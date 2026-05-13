"""
AI Teaching Personality service.

Manages the 6 teaching personality records and their assignment to students.
The system_prompt_modifier from the student's assigned personality is
prepended to AI system prompts in chat_service and lesson_qa_graph.
"""
from __future__ import annotations

from typing import List, Optional

from app.models.phase1_models import AIPersonality
from app.models.database_models import User
from app.services.school.errors import SchoolServiceError


def list_personalities(db, *, active_only: bool = True) -> List[AIPersonality]:
    q = db.query(AIPersonality)
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(AIPersonality.name).all()


def get_personality(db, *, personality_id: int) -> AIPersonality:
    p = db.query(AIPersonality).filter_by(id=personality_id).first()
    if not p:
        raise SchoolServiceError("Personality not found", "not_found", 404)
    return p


def get_default_personality(db) -> Optional[AIPersonality]:
    """Return the personality with is_default=True, or None if not seeded yet."""
    return db.query(AIPersonality).filter_by(is_default=True, is_active=True).first()


def create_personality(
    db,
    *,
    name: str,
    slug: str,
    description: Optional[str] = None,
    system_prompt_modifier: str,
    is_default: bool = False,
) -> AIPersonality:
    if db.query(AIPersonality).filter_by(slug=slug).first():
        raise SchoolServiceError(f"Personality slug '{slug}' already exists", "duplicate_slug", 409)

    if is_default:
        # Clear any existing default
        db.query(AIPersonality).filter_by(is_default=True).update({"is_default": False})

    p = AIPersonality(
        name=name,
        slug=slug,
        description=description,
        system_prompt_modifier=system_prompt_modifier,
        is_default=is_default,
    )
    db.add(p)
    db.flush()
    return p


def update_personality(
    db,
    *,
    personality_id: int,
    **fields,
) -> AIPersonality:
    p = get_personality(db, personality_id=personality_id)
    allowed = {"name", "description", "system_prompt_modifier", "is_default", "is_active"}
    for k, v in fields.items():
        if k in allowed:
            if k == "is_default" and v:
                db.query(AIPersonality).filter(AIPersonality.id != personality_id).update({"is_default": False})
            setattr(p, k, v)
    db.flush()
    return p


def delete_personality(db, *, personality_id: int) -> None:
    """Soft-delete a personality (is_active=False). Cannot delete the default."""
    p = get_personality(db, personality_id=personality_id)
    if p.is_default:
        raise SchoolServiceError("Cannot delete the default personality", "forbidden", 403)
    p.is_active = False
    db.flush()


def set_student_personality(db, *, student_id: int, personality_id: int) -> User:
    """Assign a teaching personality to a student user."""
    user = db.query(User).filter_by(id=student_id).first()
    if not user:
        raise SchoolServiceError("User not found", "not_found", 404)
    if user.role != "student":
        raise SchoolServiceError("Personalities can only be assigned to students", "forbidden", 403)
    # Verify personality exists and is active
    get_personality(db, personality_id=personality_id)
    user.personality_id = personality_id
    db.flush()
    return user


def get_system_prompt_modifier(db, *, user: User) -> str:
    """
    Return the personality system_prompt_modifier for a student.
    Returns empty string for non-student roles or if no personality is assigned.
    """
    if user.role != "student":
        return ""
    if user.personality_id is None:
        default = get_default_personality(db)
        return default.system_prompt_modifier if default else ""
    p = db.query(AIPersonality).filter_by(id=user.personality_id, is_active=True).first()
    return p.system_prompt_modifier if p else ""
