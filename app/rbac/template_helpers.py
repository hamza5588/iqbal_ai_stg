"""
Template helper functions for RBAC
These functions can be used in Jinja2 templates to conditionally show/hide UI elements
"""
from flask import session
from app.rbac.utils import (
    get_user_role,
    is_student,
    is_teacher,
    is_admin,
    get_ui_features,
    check_permission
)
from app.rbac.permissions import Permissions
from app.rbac.roles import Role


def get_current_user_role() -> str:
    """Get current user's role for templates"""
    return get_user_role()


def user_is_student() -> bool:
    """Check if current user is a student"""
    return is_student()


def user_is_teacher() -> bool:
    """Check if current user is a teacher"""
    return is_teacher()


def user_is_admin() -> bool:
    """Check if current user is an admin"""
    return is_admin()


def can_view_lesson() -> bool:
    """Check if user can view lessons"""
    return check_permission(Permissions.VIEW_LESSON)


def can_view_my_lessons() -> bool:
    """Check if user can view their own lessons (teacher)"""
    return check_permission(Permissions.VIEW_MY_LESSONS)


def can_upload_pdf() -> bool:
    """Check if user can upload PDF (teacher)"""
    return check_permission(Permissions.UPLOAD_PDF)


def can_view_all_lessons() -> bool:
    """Check if user can view all lessons (admin)"""
    return check_permission(Permissions.VIEW_ALL_LESSONS)


def can_view_all_users() -> bool:
    """Check if user can view all users (admin)"""
    return check_permission(Permissions.VIEW_ALL_USERS)


def can_view_teacher_records() -> bool:
    """Check if user can view teacher records (admin)"""
    return check_permission(Permissions.VIEW_TEACHER_RECORDS)


def can_view_student_records() -> bool:
    """Check if user can view student records (admin)"""
    return check_permission(Permissions.VIEW_STUDENT_RECORDS)


def can_view_all_records() -> bool:
    """Check if user can view all records (admin)"""
    return check_permission(Permissions.VIEW_ALL_RECORDS)


def get_role_based_features() -> dict:
    """
    Get all role-based UI features for the current user.
    Returns a dictionary with feature visibility flags.
    """
    return get_ui_features()


# Dictionary of all template helpers for easy registration
TEMPLATE_HELPERS = {
    'user_role': get_current_user_role,
    'is_student': user_is_student,
    'is_teacher': user_is_teacher,
    'is_admin': user_is_admin,
    'can_view_lesson': can_view_lesson,
    'can_view_my_lessons': can_view_my_lessons,
    'can_upload_pdf': can_upload_pdf,
    'can_view_all_lessons': can_view_all_lessons,
    'can_view_all_users': can_view_all_users,
    'can_view_teacher_records': can_view_teacher_records,
    'can_view_student_records': can_view_student_records,
    'can_view_all_records': can_view_all_records,
    'rbac_features': get_role_based_features,
}


































