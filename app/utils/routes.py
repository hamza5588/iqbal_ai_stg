"""
Routing utilities shared across the application.

This module provides small, focused helpers that do not depend on Flask
application or request context so they can be safely imported in any layer
and easily unit-tested.
"""

from typing import Optional


def get_default_route_by_role(role: Optional[str]) -> str:
    """
    Return the canonical landing route for a given user role.

    This centralises the mapping so that login / registration / guards all
    agree on where an authenticated user should be sent.

    Roles:
    - admin   -> /admin/
    - teacher -> /teacher-dashboard
    - student/other -> /student-dashboard
    """
    normalized = (role or "").strip().lower()

    if normalized == "admin":
        return "/admin/"
    if normalized == "teacher":
        return "/teacher-dashboard"

    # Default for students and any unknown roles
    return "/student-dashboard"

