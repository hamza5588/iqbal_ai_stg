"""
RBAC (Role-Based Access Control) module for IqbalAI

This module provides role-based access control functionality with support for:
- Student: Can view lessons
- Teacher: Can manage their own lessons and upload PDFs
- Admin: Can access everything (all users, all lessons, all records)
"""

from app.rbac.roles import Role
from app.rbac.permissions import Permissions, get_permissions_for_role, has_permission
from app.rbac.decorators import (
    role_required,
    permission_required,
    student_only,
    teacher_only,
    admin_only
)
from app.rbac.utils import (
    get_user_role,
    is_student,
    is_teacher,
    is_admin,
    can_access_lesson,
    can_manage_lesson,
    can_view_all_lessons,
    can_view_all_users,
    can_manage_users
)

__all__ = [
    'Role',
    'Permissions',
    'get_permissions_for_role',
    'has_permission',
    'role_required',
    'permission_required',
    'student_only',
    'teacher_only',
    'admin_only',
    'get_user_role',
    'is_student',
    'is_teacher',
    'is_admin',
    'can_access_lesson',
    'can_manage_lesson',
    'can_view_all_lessons',
    'can_view_all_users',
    'can_manage_users',
]


































