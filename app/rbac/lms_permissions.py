"""LMS-specific RBAC helpers."""
from app.services.lms import class_service


def teacher_owns_class(user_id: int, class_id: int) -> bool:
    return class_service.teacher_owns_class(user_id, class_id)


def student_in_class(user_id: int, class_id: int) -> bool:
    return class_service.student_in_class(user_id, class_id)
