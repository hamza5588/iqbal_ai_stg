"""
RBAC decorators for route protection
"""
from functools import wraps
from flask import session, redirect, url_for, request, jsonify
import logging

from app.rbac.roles import Role, is_super_admin_role
from app.rbac.permissions import Permissions, has_permission

logger = logging.getLogger(__name__)


def _wants_json() -> bool:
    """
    Helper to determine whether a request is expecting JSON.

    We treat a request as "API / JSON-style" if ANY of the following are true:
    - The request body is JSON (`request.is_json`)
    - The `Content-Type` header is `application/json`
    - The `Accept` header explicitly prefers `application/json`
    - The request was sent as an XMLHttpRequest (`X-Requested-With: XMLHttpRequest`)
    """
    content_type = (request.headers.get('Content-Type') or '').lower()
    accept = (request.headers.get('Accept') or '').lower()
    xrw = (request.headers.get('X-Requested-With') or '').lower()

    # All `/api/...` routes are treated as JSON APIs
    path = (request.path or "").lower()
    if path.startswith('/api/'):
        return True

    if request.is_json:
        return True
    if 'application/json' in content_type:
        return True
    if 'application/json' in accept:
        return True
    if xrw == 'xmlhttprequest':
        return True
    return False


def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            logger.info("Unauthorized access attempt - redirecting to login")
            if _wants_json():
                return jsonify({'error': 'Login required'}), 401
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(required_role: Role | str):
    """
    Decorator to require a specific role for routes.
    
    Args:
        required_role: Role enum or role string
        
    Example:
        @role_required(Role.ADMIN)
        def admin_route():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                logger.info("Unauthorized access attempt - redirecting to login")
                if _wants_json():
                    return jsonify({'error': 'Login required'}), 401
                return redirect(url_for('auth.login'))
            
            # Get user role
            from app.models import UserModel
            user_model = UserModel(session['user_id'])
            user_role = user_model.get_role()
            
            # Convert to Role enum for comparison
            if isinstance(required_role, str):
                required_role_enum = Role.from_string(required_role)
            else:
                required_role_enum = required_role
            
            user_role_enum = Role.from_string(user_role)
            
            if is_super_admin_role(user_role_enum):
                return f(*args, **kwargs)
            
            # Check if user has required role
            if user_role_enum != required_role_enum:
                logger.info(f"User {session['user_id']} with role {user_role} attempted to access {required_role_enum.value}-only route")
                if request.is_json or request.headers.get('Content-Type') == 'application/json':
                    return jsonify({'error': f'{required_role_enum.value.capitalize()} access required'}), 403
                return redirect(url_for('chat.index'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def permission_required(permission: Permissions | str):
    """
    Decorator to require a specific permission for routes.
    
    Args:
        permission: Permission enum or permission string
        
    Example:
        @permission_required(Permissions.VIEW_ALL_USERS)
        def view_users():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                logger.info("Unauthorized access attempt - redirecting to login")
                if _wants_json():
                    return jsonify({'error': 'Login required'}), 401
                return redirect(url_for('auth.login'))
            
            # Get user role
            from app.models import UserModel
            user_model = UserModel(session['user_id'])
            user_role = user_model.get_role()
            
            # Check permission
            if not has_permission(user_role, permission):
                logger.info(f"User {session['user_id']} with role {user_role} attempted to access route requiring {permission}")
                if request.is_json or request.headers.get('Content-Type') == 'application/json':
                    return jsonify({'error': 'Insufficient permissions'}), 403
                return redirect(url_for('chat.index'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def student_only(f):
    """Decorator to allow only students to access a route."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        from app.models import UserModel
        user_model = UserModel(session['user_id'])
        if not user_model.is_student():
            logger.info(f"Non-student user {session['user_id']} attempted to access student-only route")
            if _wants_json():
                return jsonify({'error': 'Student access required'}), 403
            return redirect(url_for('chat.index'))
        return f(*args, **kwargs)
    return decorated_function


def teacher_only(f):
    """Decorator to allow only teachers to access a route."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        from app.models import UserModel
        user_model = UserModel(session['user_id'])
        if not user_model.is_teacher():
            logger.info(f"Non-teacher user {session['user_id']} attempted to access teacher-only route")
            if _wants_json():
                return jsonify({'error': 'Teacher access required'}), 403
            return redirect(url_for('chat.index'))
        return f(*args, **kwargs)
    return decorated_function


def admin_only(f):
    """Decorator to allow only admins to access a route."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        from app.models import UserModel
        user_model = UserModel(session['user_id'])
        user_role = user_model.get_role()
        
        if not is_super_admin_role(user_role):
            logger.info(f"Non-admin user {session['user_id']} attempted to access admin-only route")
            if _wants_json():
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('chat.index'))
        return f(*args, **kwargs)
    return decorated_function


































