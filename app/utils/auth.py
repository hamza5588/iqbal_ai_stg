# from functools import wraps
# from flask import session, redirect, url_for

# def login_required(f):
#     @wraps(f)
#     def decorated_function(*args, **kwargs):
#         if 'user_id' not in session:
#             return redirect(url_for('auth.login'))
#         return f(*args, **kwargs)
#     return decorated_function 






from functools import wraps
from flask import session, redirect, url_for, request, jsonify
import logging

logger = logging.getLogger(__name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            # Check if it's an AJAX/JSON request or API endpoint
            # File uploads use multipart/form-data, so check path and headers
            request_path = request.path or ''
            content_type = request.headers.get('Content-Type', '') or ''
            method = request.method
            
            # Check for file upload (multipart/form-data) or API endpoints
            is_file_upload = content_type.startswith('multipart/form-data')
            is_api_path = (
                request_path.startswith('/api/') or
                request_path.startswith('/admin/') or
                request_path.startswith('/rag/') or
                request_path.startswith('/chatbot/')
            )
            
            # For POST requests to API paths or file uploads, ALWAYS return JSON
            # Never redirect for these requests
            if method == 'POST' and (is_api_path or is_file_upload):
                logger.warning(f"Unauthenticated API request: {method} {request_path}, Content-Type: {content_type}")
                return jsonify({
                    'error': 'Not authenticated', 
                    'redirect': url_for('auth.login'),
                    'requires_login': True
                }), 401
            
            is_api_request = (
                request.is_json or 
                content_type.startswith('application/json') or
                request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
                is_api_path or
                is_file_upload or  # File uploads should return JSON, not redirect
                request.accept_mimetypes.best_match(['application/json', 'text/html']) == 'application/json'
            )
            
            if is_api_request:
                logger.warning(f"Unauthenticated API request: {method} {request_path}")
                return jsonify({
                    'error': 'Not authenticated', 
                    'redirect': url_for('auth.login'),
                    'requires_login': True
                }), 401
            # Otherwise redirect to login page (only for non-API requests)
            logger.warning(f"Unauthenticated non-API request, redirecting: {method} {request_path}")
            return redirect(url_for('auth.login'))
        
        # Set user_id on request object for easy access
        request.user_id = session['user_id']
        return f(*args, **kwargs)
    return decorated_function
