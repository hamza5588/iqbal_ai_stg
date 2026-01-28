"""
RBAC utility functions for checking permissions and roles
"""
from flask import session
from typing import Optional

from app.rbac.roles import Role
from app.rbac.permissions import Permissions, has_permission, get_ui_features_for_role


def get_user_role(user_id: Optional[int] = None) -> str:
    """
    Get the role of a user.
    
    Args:
        user_id: Optional user ID. If not provided, uses session user_id.
        
    Returns:
        User role as string
    """
    # If no user_id provided, try to get from session
    if user_id is None:
        # First check if role is in session (faster)
        if 'role' in session:
            return session.get('role', Role.STUDENT.value)
        
        user_id = session.get('user_id')
        if not user_id:
            return Role.STUDENT.value
    
    # If user_id is provided or we got it from session, fetch from database
    from app.models import UserModel
    user_model = UserModel(user_id)
    role = user_model.get_role()
    
    # Update session if we're checking the current user
    if user_id == session.get('user_id') and 'role' not in session:
        session['role'] = role
    
    return role


def is_student(user_id: Optional[int] = None) -> bool:
    """Check if user is a student"""
    role = get_user_role(user_id)
    return Role.from_string(role) == Role.STUDENT


def is_teacher(user_id: Optional[int] = None) -> bool:
    """Check if user is a teacher"""
    role = get_user_role(user_id)
    return Role.from_string(role) == Role.TEACHER


def is_admin(user_id: Optional[int] = None) -> bool:
    """Check if user is an admin"""
    role = get_user_role(user_id)
    return Role.from_string(role) == Role.ADMIN


def check_permission(permission: Permissions | str, user_id: Optional[int] = None) -> bool:
    """
    Check if a user has a specific permission.
    
    Args:
        permission: Permission to check
        user_id: Optional user ID. If not provided, uses session user_id.
        
    Returns:
        True if user has permission, False otherwise
    """
    role = get_user_role(user_id)
    return has_permission(role, permission)


def can_access_lesson(lesson_id: int, user_id: Optional[int] = None) -> bool:
    """
    Check if a user can access a specific lesson.
    Students can access public lessons, teachers can access their own lessons,
    and admins can access all lessons.
    
    Args:
        lesson_id: ID of the lesson
        user_id: Optional user ID. If not provided, uses session user_id.
        
    Returns:
        True if user can access the lesson, False otherwise
    """
    if user_id is None:
        user_id = session.get('user_id')
        if not user_id:
            return False
    
    role = get_user_role(user_id)
    role_enum = Role.from_string(role)
    
    # Admin can access everything
    if role_enum == Role.ADMIN:
        return True
    
    # Get lesson details
    from app.utils.db import get_db
    from app.models.database_models import Lesson
    db = get_db()
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    
    if not lesson:
        return False
    
    # Teachers can access their own lessons
    if role_enum == Role.TEACHER and lesson.teacher_id == user_id:
        return True
    
    # Students can access public lessons
    if role_enum == Role.STUDENT and lesson.is_public:
        return True
    
    return False


def can_manage_lesson(lesson_id: int, user_id: Optional[int] = None) -> bool:
    """
    Check if a user can manage (edit/delete) a specific lesson.
    Teachers can manage their own lessons, admins can manage all lessons.
    
    Args:
        lesson_id: ID of the lesson
        user_id: Optional user ID. If not provided, uses session user_id.
        
    Returns:
        True if user can manage the lesson, False otherwise
    """
    if user_id is None:
        user_id = session.get('user_id')
        if not user_id:
            return False
    
    role = get_user_role(user_id)
    role_enum = Role.from_string(role)
    
    # Admin can manage everything
    if role_enum == Role.ADMIN:
        return True
    
    # Get lesson details
    from app.utils.db import get_db
    from app.models.database_models import Lesson
    db = get_db()
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    
    if not lesson:
        return False
    
    # Teachers can manage their own lessons
    if role_enum == Role.TEACHER and lesson.teacher_id == user_id:
        return True
    
    return False


def can_view_all_lessons(user_id: Optional[int] = None) -> bool:
    """Check if user can view all lessons (admin only)"""
    return check_permission(Permissions.VIEW_ALL_LESSONS, user_id)


def can_view_all_users(user_id: Optional[int] = None) -> bool:
    """Check if user can view all users (admin only)"""
    return check_permission(Permissions.VIEW_ALL_USERS, user_id)


def can_manage_users(user_id: Optional[int] = None) -> bool:
    """Check if user can manage users (admin only)"""
    return check_permission(Permissions.MANAGE_USER_ROLES, user_id)


def get_ui_features(user_id: Optional[int] = None) -> dict[str, bool]:
    """
    Get UI features visibility for the current user.
    This is used in templates to show/hide UI elements.
    
    Args:
        user_id: Optional user ID. If not provided, uses session user_id.
        
    Returns:
        Dictionary mapping feature names to visibility boolean
    """
    role = get_user_role(user_id)
    return get_ui_features_for_role(role)


def get_role_display_name(role: Role | str) -> str:
    """
    Get display name for a role.
    
    Args:
        role: Role enum or role string
        
    Returns:
        Capitalized role name
    """
    if isinstance(role, str):
        role = Role.from_string(role)
    return role.value.capitalize()

