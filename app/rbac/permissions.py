"""
Permission definitions for RBAC system

Each role has specific permissions that determine what actions they can perform.
"""
from enum import Enum
from typing import Set
from app.rbac.roles import Role


class Permissions(str, Enum):
    """Available permissions in the system"""
    # Lesson permissions
    VIEW_LESSON = "view_lesson"  # View any lesson (student can view public lessons)
    VIEW_MY_LESSONS = "view_my_lessons"  # View own lessons (teacher)
    VIEW_ALL_LESSONS = "view_all_lessons"  # View all lessons (admin)
    CREATE_LESSON = "create_lesson"  # Create new lessons (teacher)
    EDIT_MY_LESSON = "edit_my_lesson"  # Edit own lessons (teacher)
    EDIT_ANY_LESSON = "edit_any_lesson"  # Edit any lesson (admin)
    DELETE_MY_LESSON = "delete_my_lesson"  # Delete own lessons (teacher)
    DELETE_ANY_LESSON = "delete_any_lesson"  # Delete any lesson (admin)
    
    # PDF/Document permissions
    UPLOAD_PDF = "upload_pdf"  # Upload PDF documents (teacher)
    VIEW_MY_DOCUMENTS = "view_my_documents"  # View own documents (teacher)
    VIEW_ALL_DOCUMENTS = "view_all_documents"  # View all documents (admin)
    DELETE_MY_DOCUMENT = "delete_my_document"  # Delete own documents (teacher)
    DELETE_ANY_DOCUMENT = "delete_any_document"  # Delete any document (admin)
    
    # User management permissions
    VIEW_MY_PROFILE = "view_my_profile"  # View own profile (all roles)
    EDIT_MY_PROFILE = "edit_my_profile"  # Edit own profile (all roles)
    VIEW_ALL_USERS = "view_all_users"  # View all users (admin)
    EDIT_ANY_USER = "edit_any_user"  # Edit any user (admin)
    DELETE_ANY_USER = "delete_any_user"  # Delete any user (admin)
    MANAGE_USER_ROLES = "manage_user_roles"  # Change user roles (admin)
    
    # Teacher records permissions (admin only)
    VIEW_TEACHER_RECORDS = "view_teacher_records"  # View all teacher records (admin)
    VIEW_STUDENT_RECORDS = "view_student_records"  # View all student records (admin)
    VIEW_ALL_RECORDS = "view_all_records"  # View all records (admin)
    
    # Chat/Conversation permissions
    CREATE_CONVERSATION = "create_conversation"  # Create conversations (all roles)
    VIEW_MY_CONVERSATIONS = "view_my_conversations"  # View own conversations (all roles)
    VIEW_ALL_CONVERSATIONS = "view_all_conversations"  # View all conversations (admin)

    # Phase 1 permissions
    INVITE_USER = "invite_user"  # Invite/create lower-role users
    SUSPEND_USER = "suspend_user"  # Suspend and reactivate user accounts
    VIEW_CONTENT_LIBRARY = "view_content_library"  # Browse content library books
    MANAGE_CONTENT_LIBRARY = "manage_content_library"  # Upload/edit/delete content books
    MANAGE_SYLLABUS = "manage_syllabus"  # CRUD exam syllabus tree
    MANAGE_PERSONALITIES = "manage_personalities"  # CRUD AI teaching personalities
    VIEW_NOTIFICATIONS = "view_notifications"  # View own notification inbox
    MANAGE_PARENT_LINKS = "manage_parent_links"  # Approve/reject parent-student links
    EXPORT_STUDENT_DATA = "export_student_data"  # Export own student data

    def __str__(self):
        return self.value


# Define permissions for each role
ROLE_PERMISSIONS: dict[Role, Set[Permissions]] = {
    Role.STUDENT: {
        Permissions.VIEW_LESSON,
        Permissions.VIEW_MY_PROFILE,
        Permissions.EDIT_MY_PROFILE,
        Permissions.CREATE_CONVERSATION,
        Permissions.VIEW_MY_CONVERSATIONS,
        Permissions.VIEW_CONTENT_LIBRARY,
        Permissions.VIEW_NOTIFICATIONS,
        Permissions.MANAGE_PARENT_LINKS,
        Permissions.EXPORT_STUDENT_DATA,
    },
    
    Role.TEACHER: {
        Permissions.VIEW_LESSON,
        Permissions.VIEW_MY_LESSONS,
        Permissions.CREATE_LESSON,
        Permissions.EDIT_MY_LESSON,
        Permissions.DELETE_MY_LESSON,
        Permissions.UPLOAD_PDF,
        Permissions.VIEW_MY_DOCUMENTS,
        Permissions.DELETE_MY_DOCUMENT,
        Permissions.VIEW_MY_PROFILE,
        Permissions.EDIT_MY_PROFILE,
        Permissions.CREATE_CONVERSATION,
        Permissions.VIEW_MY_CONVERSATIONS,
    },
    
    Role.ADMIN: {
        # Admin has all permissions
        Permissions.VIEW_LESSON,
        Permissions.VIEW_MY_LESSONS,
        Permissions.VIEW_ALL_LESSONS,
        Permissions.CREATE_LESSON,
        Permissions.EDIT_MY_LESSON,
        Permissions.EDIT_ANY_LESSON,
        Permissions.DELETE_MY_LESSON,
        Permissions.DELETE_ANY_LESSON,
        Permissions.UPLOAD_PDF,
        Permissions.VIEW_MY_DOCUMENTS,
        Permissions.VIEW_ALL_DOCUMENTS,
        Permissions.DELETE_MY_DOCUMENT,
        Permissions.DELETE_ANY_DOCUMENT,
        Permissions.VIEW_MY_PROFILE,
        Permissions.EDIT_MY_PROFILE,
        Permissions.VIEW_ALL_USERS,
        Permissions.EDIT_ANY_USER,
        Permissions.DELETE_ANY_USER,
        Permissions.MANAGE_USER_ROLES,
        Permissions.VIEW_TEACHER_RECORDS,
        Permissions.VIEW_STUDENT_RECORDS,
        Permissions.VIEW_ALL_RECORDS,
        Permissions.CREATE_CONVERSATION,
        Permissions.VIEW_MY_CONVERSATIONS,
        Permissions.VIEW_ALL_CONVERSATIONS,
        # Phase 1
        Permissions.INVITE_USER,
        Permissions.SUSPEND_USER,
        Permissions.VIEW_CONTENT_LIBRARY,
        Permissions.MANAGE_CONTENT_LIBRARY,
        Permissions.MANAGE_SYLLABUS,
        Permissions.MANAGE_PERSONALITIES,
        Permissions.VIEW_NOTIFICATIONS,
        Permissions.MANAGE_PARENT_LINKS,
        Permissions.EXPORT_STUDENT_DATA,
    },
    # School chain: global permissions today; route-level school scoping comes later.
    Role.PLATFORM_ADMIN: set(),  # filled below
    Role.DISTRICT_ADMIN: set(),
    Role.SCHOOL_ADMIN: set(),
    Role.PRINCIPAL: set(),
    Role.COORDINATOR: set(),
    Role.PARENT: {
        Permissions.VIEW_MY_PROFILE,
        Permissions.EDIT_MY_PROFILE,
        Permissions.VIEW_LESSON,
        Permissions.VIEW_MY_CONVERSATIONS,
        Permissions.VIEW_NOTIFICATIONS,
        Permissions.MANAGE_PARENT_LINKS,
    },
}

_FULL_ADMIN = ROLE_PERMISSIONS[Role.ADMIN]
ROLE_PERMISSIONS[Role.PLATFORM_ADMIN] = set(_FULL_ADMIN)
ROLE_PERMISSIONS[Role.DISTRICT_ADMIN] = set(_FULL_ADMIN)
ROLE_PERMISSIONS[Role.SCHOOL_ADMIN] = set(_FULL_ADMIN)

_SCHOOL_OVERSIGHT = {
    Permissions.VIEW_LESSON,
    Permissions.VIEW_MY_LESSONS,
    Permissions.VIEW_ALL_LESSONS,
    Permissions.CREATE_LESSON,
    Permissions.EDIT_MY_LESSON,
    Permissions.DELETE_MY_LESSON,
    Permissions.UPLOAD_PDF,
    Permissions.VIEW_MY_DOCUMENTS,
    Permissions.DELETE_MY_DOCUMENT,
    Permissions.VIEW_MY_PROFILE,
    Permissions.EDIT_MY_PROFILE,
    Permissions.VIEW_TEACHER_RECORDS,
    Permissions.VIEW_STUDENT_RECORDS,
    Permissions.VIEW_ALL_RECORDS,
    Permissions.CREATE_CONVERSATION,
    Permissions.VIEW_MY_CONVERSATIONS,
    Permissions.VIEW_ALL_CONVERSATIONS,
    # Phase 1
    Permissions.VIEW_CONTENT_LIBRARY,
    Permissions.VIEW_NOTIFICATIONS,
}
ROLE_PERMISSIONS[Role.PRINCIPAL] = set(_SCHOOL_OVERSIGHT)
ROLE_PERMISSIONS[Role.COORDINATOR] = set(_SCHOOL_OVERSIGHT)


def get_permissions_for_role(role: Role | str) -> Set[Permissions]:
    """
    Get all permissions for a given role.
    
    Args:
        role: Role enum or role string
        
    Returns:
        Set of permissions for the role
    """
    if isinstance(role, str):
        role = Role.from_string(role)
    
    return ROLE_PERMISSIONS.get(role, set())


def has_permission(role: Role | str, permission: Permissions | str) -> bool:
    """
    Check if a role has a specific permission.
    
    Args:
        role: Role enum or role string
        permission: Permission enum or permission string
        
    Returns:
        True if role has the permission, False otherwise
    """
    if isinstance(role, str):
        role = Role.from_string(role)
    
    if isinstance(permission, str):
        try:
            permission = Permissions(permission)
        except ValueError:
            return False
    
    role_perms = get_permissions_for_role(role)
    return permission in role_perms


def get_ui_features_for_role(role: Role | str) -> dict[str, bool]:
    """
    Get UI features visibility for a role.
    This is used to determine what UI elements to show/hide.
    
    Args:
        role: Role enum or role string
        
    Returns:
        Dictionary mapping feature names to visibility boolean
    """
    if isinstance(role, str):
        role = Role.from_string(role)
    
    perms = get_permissions_for_role(role)
    
    return {
        'view_lesson': Permissions.VIEW_LESSON in perms,
        'my_lessons': Permissions.VIEW_MY_LESSONS in perms,
        'upload_pdf': Permissions.UPLOAD_PDF in perms,
        'view_all_lessons': Permissions.VIEW_ALL_LESSONS in perms,
        'view_all_users': Permissions.VIEW_ALL_USERS in perms,
        'view_teacher_records': Permissions.VIEW_TEACHER_RECORDS in perms,
        'view_student_records': Permissions.VIEW_STUDENT_RECORDS in perms,
        'view_all_records': Permissions.VIEW_ALL_RECORDS in perms,
        'manage_users': Permissions.MANAGE_USER_ROLES in perms,
    }

