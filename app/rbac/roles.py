"""
Role definitions for RBAC system
"""
from enum import Enum


class Role(str, Enum):
    """User roles in the system"""
    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"
    
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


































