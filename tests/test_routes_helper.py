import pytest

from app.utils.routes import get_default_route_by_role


@pytest.mark.parametrize(
    "role,expected",
    [
        ("admin", "/admin/"),
        ("ADMIN", "/admin/"),
        ("teacher", "/teacher-dashboard"),
        ("Teacher", "/teacher-dashboard"),
        ("student", "/student-dashboard"),
        ("STUDENT", "/student-dashboard"),
        ("", "/student-dashboard"),
        (None, "/student-dashboard"),
        ("unknown-role", "/student-dashboard"),
    ],
)
def test_get_default_route_by_role(role, expected):
    assert get_default_route_by_role(role) == expected

