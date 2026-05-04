"""
Role definitions for RBAC system
"""
from enum import Enum
from typing import FrozenSet


class Role(str, Enum):
    """User roles in the system"""
    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"
    PRINCIPAL = "principal"
    COORDINATOR = "coordinator"
    SCHOOL_ADMIN = "school_admin"
    DISTRICT_ADMIN = "district_admin"
    PLATFORM_ADMIN = "platform_admin"
    PARENT = "parent"
    
    def __str__(self):
        return self.value
    
    @classmethod
    def from_string(cls, role_str: str) -> 'Role':
        """Convert string to Role enum"""
        role_str = role_str.lower().strip()
        for role in cls:
            if role.value == role_str:
                return role
        # Default to student if invalid role
        return cls.STUDENT
    
    @classmethod
    def is_valid(cls, role_str: str) -> bool:
        """Check if a string is a valid role"""
        role_str = role_str.lower().strip()
        return role_str in [role.value for role in cls]
    
    @classmethod
    def get_all(cls) -> list[str]:
        """Get all role values as strings"""
        return [role.value for role in cls]


# Roles that may use legacy "admin" routes until those are split by scope.
SUPER_ADMIN_ROLES: FrozenSet[Role] = frozenset(
    {
        Role.ADMIN,
        Role.PLATFORM_ADMIN,
        Role.DISTRICT_ADMIN,
        Role.SCHOOL_ADMIN,
    }
)


def is_super_admin_role(role: Role | str) -> bool:
    if isinstance(role, str):
        role = Role.from_string(role)
    return role in SUPER_ADMIN_ROLES


































