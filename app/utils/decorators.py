from functools import wraps
from flask import session, redirect, url_for, request, jsonify
import logging

logger = logging.getLogger(__name__)

def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            logger.info("Unauthorized access attempt - redirecting to login")
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def teacher_required(f):
    """Decorator to require teacher role for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            logger.info("Unauthorized access attempt - redirecting to login")
            return redirect(url_for('auth.login'))
        
        # Check if user is a teacher
        from app.models import UserModel
        user_model = UserModel(session['user_id'])
        if not user_model.is_teacher():
            logger.info(f"Non-teacher user {session['user_id']} attempted to access teacher-only route")
            if request.is_json or request.headers.get('Content-Type') == 'application/json':
                return jsonify({'error': 'Teacher access required'}), 403
            return redirect(url_for('chat.index'))
        
        return f(*args, **kwargs)
    return decorated_function

def student_required(f):
    """Decorator to require student role for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            logger.info("Unauthorized access attempt - redirecting to login")
            return redirect(url_for('auth.login'))
        
        # Check if user is a student
        from app.models import UserModel
        user_model = UserModel(session['user_id'])
        if not user_model.is_student():
            logger.info(f"Non-student user {session['user_id']} attempted to access student-only route")
            if request.is_json or request.headers.get('Content-Type') == 'application/json':
                return jsonify({'error': 'Student access required'}), 403
            return redirect(url_for('chat.index'))
        
        return f(*args, **kwargs)
    return decorated_function

def role_required(required_role):
    """Decorator to require a specific role for routes."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                logger.info("Unauthorized access attempt - redirecting to login")
                return redirect(url_for('auth.login'))
            
            # Check if user has the required role
            from app.models import UserModel
            user_model = UserModel(session['user_id'])
            if user_model.get_role() != required_role:
                logger.info(f"User {session['user_id']} with role {user_model.get_role()} attempted to access {required_role}-only route")
                if request.is_json or request.headers.get('Content-Type') == 'application/json':
                    return jsonify({'error': f'{required_role.capitalize()} access required'}), 403
                return redirect(url_for('chat.index'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator
