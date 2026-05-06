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
    - admin, platform_admin, district_admin, school_admin -> /admin/
    - coordinator -> /school/coordinator-dashboard
    - principal  -> /school/principal-dashboard
    - teacher    -> /teacher-dashboard
    - parent     -> /school/hub
    - student, other -> /student-dashboard
    """
    normalized = (role or "").strip().lower()

    if normalized in (
        "admin",
        "platform_admin",
        "district_admin",
        "school_admin",
    ):
        return "/admin/"
    if normalized == "teacher":
        return "/teacher-dashboard"
    if normalized == "coordinator":
        return "/school/coordinator-dashboard"
    if normalized == "principal":
        return "/school/principal-dashboard"
    if normalized == "parent":
        return "/school/hub"

    # Default for students and any unknown roles
    return "/student-dashboard"

