"""
Admin routes for system management
Provides comprehensive admin functionality including user management, 
prompt management, coupon management, and lesson management.
"""
from flask import Blueprint, request, jsonify, session, render_template
from app.utils.auth import login_required
from app.rbac.decorators import admin_only
from app.models import UserModel
from app.models.models import LessonModel
from app.utils.db import get_db
from app.models.database_models import (
    User as DBUser, 
    UserDocument, 
    Coupon, 
    CouponRedemption,
    Lesson as DBLesson,
    UserPrompt,
    RAGPrompt,
    GlobalPrompt,
    SystemSettings,
    UserSettings
)
from sqlalchemy import or_, func, desc
from datetime import datetime
import logging
import os

logger = logging.getLogger(__name__)
bp = Blueprint('admin', __name__, url_prefix='/admin')


def is_admin():
    """Check if current user is admin"""
    if 'user_id' not in session:
        return False
    try:
        user_model = UserModel(session['user_id'])
        return user_model.is_admin()
    except:
        return False


# ==================== DASHBOARD ====================

@bp.route('/')
@login_required
@admin_only
def dashboard():
    """Admin dashboard"""
    try:
        db = get_db()
        
        # Get statistics
        total_users = db.query(DBUser).count()
        total_teachers = db.query(DBUser).filter(DBUser.role == 'teacher').count()
        total_students = db.query(DBUser).filter(DBUser.role == 'student').count()
        total_lessons = db.query(DBLesson).count()
        total_documents = db.query(UserDocument).count()
        total_coupons = db.query(Coupon).count()
        
        stats = {
            'total_users': total_users,
            'total_teachers': total_teachers,
            'total_students': total_students,
            'total_lessons': total_lessons,
            'total_documents': total_documents,
            'total_coupons': total_coupons
        }
        
        return render_template('admin/dashboard.html', stats=stats)
    except Exception as e:
        logger.error(f"Error loading admin dashboard: {str(e)}", exc_info=True)
        return render_template('admin/dashboard.html', stats={
            'total_users': 0,
            'total_teachers': 0,
            'total_students': 0,
            'total_lessons': 0,
            'total_documents': 0,
            'total_coupons': 0
        }, error=str(e))


# ==================== USER MANAGEMENT ====================

@bp.route('/users', methods=['GET'])
@login_required
@admin_only
def list_users():
    """List all users with filtering"""
    try:
        db = get_db()
        role_filter = request.args.get('role', 'all')  # all, teacher, student, admin
        search = request.args.get('search', '').strip()
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        
        query = db.query(DBUser)
        
        # Apply role filter
        if role_filter != 'all':
            query = query.filter(DBUser.role == role_filter)
        
        # Apply search filter
        if search:
            query = query.filter(
                or_(
                    DBUser.username.ilike(f'%{search}%'),
                    DBUser.useremail.ilike(f'%{search}%')
                )
            )
        
        # Get total count
        total = query.count()
        
        # Paginate
        users = query.order_by(DBUser.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
        
        users_list = []
        for user in users:
            users_list.append({
                'id': user.id,
                'username': user.username,
                'useremail': user.useremail,
                'role': user.role,
                'class_standard': user.class_standard or '',
                'medium': user.medium or '',
                'subscription_tier': user.subscription_tier or 'free',
                'created_at': user.created_at.isoformat() if user.created_at else None
            })
        
        return jsonify({
            'success': True,
            'users': users_list,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        })
    except Exception as e:
        logger.error(f"Error listing users: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/users', methods=['POST'])
@login_required
@admin_only
def create_user():
    """Create a new user account"""
    try:
        data = request.json
        username = data.get('username', '').strip()
        useremail = data.get('useremail', '').strip()
        password = data.get('password', '').strip()
        role = data.get('role', 'student').strip()
        class_standard = data.get('class_standard', '').strip()
        medium = data.get('medium', '').strip()
        
        # Validation
        if not username or not useremail or not password:
            return jsonify({'success': False, 'error': 'Username, email, and password are required'}), 400
        
        if role not in ['student', 'teacher', 'admin']:
            return jsonify({'success': False, 'error': 'Invalid role'}), 400
        
        # Check if user already exists
        db = get_db()
        existing = db.query(DBUser).filter(
            or_(DBUser.username == username, DBUser.useremail == useremail)
        ).first()
        
        if existing:
            return jsonify({'success': False, 'error': 'Username or email already exists'}), 400
        
        # Create user
        user_id = UserModel.create_user(
            username=username,
            useremail=useremail,
            password=password,
            class_standard=class_standard,
            medium=medium,
            groq_api_key='',
            role=role
        )
        
        return jsonify({
            'success': True,
            'message': 'User created successfully',
            'user_id': user_id
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating user: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/users/<int:user_id>', methods=['GET'])
@login_required
@admin_only
def get_user(user_id):
    """Get a single user by ID"""
    try:
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == user_id).first()
        
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        return jsonify({
            'success': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'useremail': user.useremail,
                'role': user.role,
                'class_standard': user.class_standard or '',
                'medium': user.medium or '',
                'subscription_tier': user.subscription_tier or 'free',
                'created_at': user.created_at.isoformat() if user.created_at else None
            }
        })
    except Exception as e:
        logger.error(f"Error getting user: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/users/<int:user_id>', methods=['PUT'])
@login_required
@admin_only
def update_user(user_id):
    """Update user account"""
    try:
        data = request.json
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == user_id).first()
        
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        # Update fields
        if 'username' in data:
            user.username = data['username'].strip()
        if 'useremail' in data:
            user.useremail = data['useremail'].strip()
        if 'password' in data and data['password']:
            user.password = data['password'].strip()
        if 'role' in data:
            if data['role'] in ['student', 'teacher', 'admin']:
                user.role = data['role']
        if 'class_standard' in data:
            user.class_standard = data['class_standard'].strip()
        if 'medium' in data:
            user.medium = data['medium'].strip()
        if 'subscription_tier' in data:
            user.subscription_tier = data['subscription_tier']
        
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'User updated successfully'
        })
    except Exception as e:
        logger.error(f"Error updating user: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/users/<int:user_id>', methods=['DELETE'])
@login_required
@admin_only
def delete_user(user_id):
    """Delete user account"""
    try:
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == user_id).first()
        
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        # Prevent deleting admin account
        if user.username == 'admin' and user.role == 'admin':
            return jsonify({'success': False, 'error': 'Cannot delete default admin account'}), 400
        
        db.delete(user)
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'User deleted successfully'
        })
    except Exception as e:
        logger.error(f"Error deleting user: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/users/<int:user_id>/change-password', methods=['POST'])
@login_required
@admin_only
def change_user_password(user_id):
    """Change user password"""
    try:
        data = request.json
        new_password = data.get('password', '').strip()
        
        if not new_password:
            return jsonify({'success': False, 'error': 'Password is required'}), 400
        
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == user_id).first()
        
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        user.password = new_password
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Password changed successfully'
        })
    except Exception as e:
        logger.error(f"Error changing password: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== PDF/DOCUMENT MANAGEMENT ====================

@bp.route('/documents', methods=['GET'])
@login_required
@admin_only
def list_documents():
    """List all documents with filtering"""
    try:
        db = get_db()
        user_id = request.args.get('user_id', type=int)
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        
        query = db.query(UserDocument)
        
        if user_id:
            query = query.filter(UserDocument.user_id == user_id)
        
        total = query.count()
        documents = query.order_by(UserDocument.uploaded_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
        
        docs_list = []
        for doc in documents:
            user = db.query(DBUser).filter(DBUser.id == doc.user_id).first()
            docs_list.append({
                'id': doc.id,
                'user_id': doc.user_id,
                'username': user.username if user else 'Unknown',
                'file_name': doc.file_name,
                'file_size': doc.file_size,
                'file_type': doc.file_type,
                'processed': doc.processed,
                'uploaded_at': doc.uploaded_at.isoformat() if doc.uploaded_at else None
            })
        
        return jsonify({
            'success': True,
            'documents': docs_list,
            'total': total,
            'page': page,
            'per_page': per_page
        })
    except Exception as e:
        logger.error(f"Error listing documents: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/documents/<int:doc_id>', methods=['DELETE'])
@login_required
@admin_only
def delete_document(doc_id):
    """Delete a document"""
    try:
        db = get_db()
        doc = db.query(UserDocument).filter(UserDocument.id == doc_id).first()
        
        if not doc:
            return jsonify({'success': False, 'error': 'Document not found'}), 404
        
        # Delete file from filesystem if it exists
        if doc.file_path and os.path.exists(doc.file_path):
            try:
                os.remove(doc.file_path)
            except Exception as e:
                logger.warning(f"Error deleting file {doc.file_path}: {str(e)}")
        
        db.delete(doc)
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Document deleted successfully'
        })
    except Exception as e:
        logger.error(f"Error deleting document: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== GLOBAL PROMPT MANAGEMENT ====================

@bp.route('/prompt/global', methods=['GET'])
@login_required
@admin_only
def get_global_prompt():
    """Get global system prompt"""
    try:
        db = get_db()
        # Get the global prompt (there should only be one)
        global_prompt = db.query(GlobalPrompt).first()
        
        if global_prompt:
            return jsonify({
                'success': True,
                'prompt': global_prompt.prompt,
                'updated_at': global_prompt.updated_at.isoformat() if global_prompt.updated_at else None
            })
        else:
            return jsonify({
                'success': True,
                'prompt': '',
                'updated_at': None
            })
    except Exception as e:
        logger.error(f"Error getting global prompt: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/prompt/global', methods=['POST'])
@login_required
@admin_only
def set_global_prompt():
    """Set or update global system prompt"""
    try:
        data = request.json
        prompt = data.get('prompt', '').strip()
        
        db = get_db()
        
        # Check if global prompt exists
        global_prompt = db.query(GlobalPrompt).first()
        
        if global_prompt:
            # Update existing
            global_prompt.prompt = prompt
            global_prompt.updated_at = datetime.utcnow()
            global_prompt.updated_by = session['user_id']
        else:
            # Create new (only one should exist - application logic ensures this)
            global_prompt = GlobalPrompt(
                prompt=prompt,
                updated_by=session['user_id']
            )
            db.add(global_prompt)
        
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Global prompt updated successfully'
        })
    except Exception as e:
        logger.error(f"Error setting global prompt: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/prompt/global', methods=['DELETE'])
@login_required
@admin_only
def delete_global_prompt():
    """Delete global system prompt"""
    try:
        db = get_db()
        global_prompt = db.query(GlobalPrompt).first()
        
        if global_prompt:
            db.delete(global_prompt)
            db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Global prompt deleted successfully'
        })
    except Exception as e:
        logger.error(f"Error deleting global prompt: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== COUPON MANAGEMENT ====================

@bp.route('/coupons', methods=['GET'])
@login_required
@admin_only
def list_coupons():
    """List all coupons"""
    try:
        db = get_db()
        coupons = db.query(Coupon).order_by(Coupon.created_at.desc()).all()
        
        coupons_list = []
        for coupon in coupons:
            coupons_list.append({
                'id': coupon.id,
                'code': coupon.code,
                'subscription_tier': coupon.subscription_tier,
                'description': coupon.description,
                'max_uses': coupon.max_uses,
                'used_count': coupon.used_count,
                'expires_at': coupon.expires_at.isoformat() if coupon.expires_at else None,
                'is_active': coupon.is_active,
                'created_at': coupon.created_at.isoformat() if coupon.created_at else None
            })
        
        return jsonify({
            'success': True,
            'coupons': coupons_list
        })
    except Exception as e:
        logger.error(f"Error listing coupons: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/coupons', methods=['POST'])
@login_required
@admin_only
def create_coupon():
    """Create a new coupon"""
    try:
        data = request.json
        code = data.get('code', '').strip().upper()
        subscription_tier = data.get('subscription_tier', 'pro').strip()
        description = data.get('description', '').strip()
        max_uses = data.get('max_uses')
        expires_at_str = data.get('expires_at')
        
        if not code:
            return jsonify({'success': False, 'error': 'Coupon code is required'}), 400
        
        if subscription_tier not in ['pro', 'pro_plus']:
            return jsonify({'success': False, 'error': 'Invalid subscription tier'}), 400
        
        db = get_db()
        
        # Check if coupon already exists
        existing = db.query(Coupon).filter(Coupon.code == code).first()
        if existing:
            return jsonify({'success': False, 'error': 'Coupon code already exists'}), 400
        
        # Parse expiration date
        expires_at = None
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
            except:
                return jsonify({'success': False, 'error': 'Invalid expiration date format'}), 400
        
        # Create coupon
        coupon = Coupon(
            code=code,
            subscription_tier=subscription_tier,
            description=description,
            max_uses=max_uses if max_uses else None,
            expires_at=expires_at,
            is_active=True,
            created_by=session['user_id']
        )
        
        db.add(coupon)
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Coupon created successfully',
            'coupon': {
                'id': coupon.id,
                'code': coupon.code,
                'subscription_tier': coupon.subscription_tier
            }
        })
    except Exception as e:
        logger.error(f"Error creating coupon: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/coupons/<int:coupon_id>', methods=['DELETE'])
@login_required
@admin_only
def delete_coupon(coupon_id):
    """Delete a coupon"""
    try:
        db = get_db()
        coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
        
        if not coupon:
            return jsonify({'success': False, 'error': 'Coupon not found'}), 404
        
        db.delete(coupon)
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Coupon deleted successfully'
        })
    except Exception as e:
        logger.error(f"Error deleting coupon: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== LESSON MANAGEMENT ====================

@bp.route('/lessons', methods=['GET'])
@login_required
@admin_only
def list_lessons():
    """List all lessons"""
    try:
        db = get_db()
        teacher_id = request.args.get('teacher_id', type=int)
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        
        query = db.query(DBLesson)
        
        if teacher_id:
            query = query.filter(DBLesson.teacher_id == teacher_id)
        
        total = query.count()
        lessons = query.order_by(DBLesson.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
        
        lessons_list = []
        for lesson in lessons:
            teacher = db.query(DBUser).filter(DBUser.id == lesson.teacher_id).first()
            lessons_list.append({
                'id': lesson.id,
                'title': lesson.title,
                'teacher_id': lesson.teacher_id,
                'teacher_name': teacher.username if teacher else 'Unknown',
                'created_at': lesson.created_at.isoformat() if lesson.created_at else None
            })
        
        return jsonify({
            'success': True,
            'lessons': lessons_list,
            'total': total,
            'page': page,
            'per_page': per_page
        })
    except Exception as e:
        logger.error(f"Error listing lessons: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/lessons/<int:lesson_id>', methods=['DELETE'])
@login_required
@admin_only
def delete_lesson(lesson_id):
    """Delete a lesson"""
    try:
        db = get_db()
        lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
        
        if not lesson:
            return jsonify({'success': False, 'error': 'Lesson not found'}), 404
        
        db.delete(lesson)
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Lesson deleted successfully'
        })
    except Exception as e:
        logger.error(f"Error deleting lesson: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/lessons/create-as-teacher', methods=['POST'])
@login_required
@admin_only
def create_lesson_as_teacher():
    """Create a lesson using a teacher's account (admin can create lessons as any teacher)"""
    try:
        data = request.json
        teacher_id = data.get('teacher_id', type=int)
        
        if not teacher_id:
            return jsonify({'success': False, 'error': 'Teacher ID is required'}), 400
        
        db = get_db()
        teacher = db.query(DBUser).filter(
            DBUser.id == teacher_id,
            DBUser.role == 'teacher'
        ).first()
        
        if not teacher:
            return jsonify({'success': False, 'error': 'Teacher not found'}), 404
        
        # Temporarily set session to teacher's ID for lesson creation
        # This allows admin to create lessons as that teacher
        original_user_id = session.get('user_id')
        session['user_id'] = teacher_id
        
        try:
            # Use the lesson creation endpoint logic
            # You'll need to adapt this based on your lesson creation logic
            # For now, return a message indicating this feature needs implementation
            return jsonify({
                'success': True,
                'message': 'Lesson creation as teacher feature - redirect to lesson creation with teacher context',
                'teacher_id': teacher_id,
                'teacher_name': teacher.username
            })
        finally:
            # Restore original session
            session['user_id'] = original_user_id
            
    except Exception as e:
        logger.error(f"Error creating lesson as teacher: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== SYSTEM SETTINGS MANAGEMENT ====================

@bp.route('/settings/llm-provider', methods=['GET'])
@login_required
def get_llm_provider():
    """Get current LLM provider setting (accessible to all authenticated users for UI display)"""
    """Get current LLM provider setting"""
    try:
        db = get_db()
        setting = db.query(SystemSettings).filter(SystemSettings.key == 'llm_provider').first()
        
        if setting:
            return jsonify({
                'success': True,
                'provider': setting.value
            })
        else:
            # Default to OpenAI if not set
            return jsonify({
                'success': True,
                'provider': 'openai'
            })
    except Exception as e:
        logger.error(f"Error getting LLM provider: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/settings/llm-provider', methods=['POST'])
@login_required
@admin_only
def set_llm_provider():
    """Set LLM provider (openai or groq) - DEPRECATED: Use /settings/llm instead"""
    try:
        data = request.json
        provider = data.get('provider', '').strip().upper()
        
        if provider not in ['OPENAI', 'GROQ']:
            return jsonify({'success': False, 'error': 'Provider must be "OPENAI" or "GROQ"'}), 400
        
        db = get_db()
        setting = db.query(SystemSettings).filter(SystemSettings.key == 'active_provider').first()
        
        if setting:
            setting.value = provider
            setting.updated_at = datetime.utcnow()
            setting.updated_by = session['user_id']
        else:
            setting = SystemSettings(
                key='active_provider',
                value=provider,
                description='Active LLM Provider: OPENAI or GROQ',
                updated_by=session['user_id']
            )
            db.add(setting)
        
        db.commit()
        
        return jsonify({
            'success': True,
            'message': f'LLM provider set to {provider}',
            'provider': provider
        })
    except Exception as e:
        logger.error(f"Error setting LLM provider: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== LLM SETTINGS MANAGEMENT ====================

@bp.route('/settings/llm', methods=['GET'])
@login_required
@admin_only
def get_llm_settings():
    """Get all LLM settings (admin only)"""
    try:
        from app.utils.encryption import mask_api_key
        from app.utils.llm_models import GROQ_MODELS, OPENAI_MODELS
        
        db = get_db()
        
        # Get all LLM-related settings
        settings = db.query(SystemSettings).filter(
            SystemSettings.key.in_([
                'active_provider',
                'groq_api_key',
                'openai_api_key',
                'groq_default_model',
                'openai_default_model',
                'groq_allow_user_model_selection',
                'openai_allow_user_model_selection'
            ])
        ).all()
        
        # Convert to dict
        settings_dict = {s.key: s.value for s in settings}
        
        # Mask API keys
        result = {
            'active_provider': settings_dict.get('active_provider', 'OPENAI'),
            'groq_api_key_set': bool(settings_dict.get('groq_api_key')),
            'groq_api_key_masked': mask_api_key(settings_dict.get('groq_api_key', '')) if settings_dict.get('groq_api_key') else None,
            'openai_api_key_set': bool(settings_dict.get('openai_api_key')),
            'openai_api_key_masked': mask_api_key(settings_dict.get('openai_api_key', '')) if settings_dict.get('openai_api_key') else None,
            'groq_default_model': settings_dict.get('groq_default_model', 'llama-3.3-70b-versatile'),
            'openai_default_model': settings_dict.get('openai_default_model', 'gpt-4o-mini'),
            'groq_allow_user_model_selection': settings_dict.get('groq_allow_user_model_selection', 'false').lower() == 'true',
            'openai_allow_user_model_selection': settings_dict.get('openai_allow_user_model_selection', 'false').lower() == 'true',
            'available_models': {
                'groq': {
                    'qwen': GROQ_MODELS['qwen'],
                    'llama': GROQ_MODELS['llama']
                },
                'openai': OPENAI_MODELS
            }
        }
        
        return jsonify({
            'success': True,
            'settings': result
        })
    except Exception as e:
        logger.error(f"Error getting LLM settings: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/settings/llm', methods=['PUT'])
@login_required
@admin_only
def update_llm_settings():
    """Update LLM settings (admin only)"""
    try:
        from app.utils.encryption import encrypt_api_key
        
        data = request.json
        db = get_db()
        
        # Helper to update/create setting
        def set_setting(key, value, description=None):
            setting = db.query(SystemSettings).filter(SystemSettings.key == key).first()
            if setting:
                setting.value = value
                setting.updated_at = datetime.utcnow()
                setting.updated_by = session['user_id']
                if description:
                    setting.description = description
            else:
                setting = SystemSettings(
                    key=key,
                    value=value,
                    description=description or f'{key} setting',
                    updated_by=session['user_id']
                )
                db.add(setting)
        
        # Update active provider
        if 'active_provider' in data:
            provider = data['active_provider'].strip().upper()
            if provider not in ['OPENAI', 'GROQ']:
                return jsonify({'success': False, 'error': 'Provider must be "OPENAI" or "GROQ"'}), 400
            set_setting('active_provider', provider, 'Active LLM Provider: OPENAI or GROQ')
        
        # Update API keys (encrypt before storing)
        if 'groq_api_key' in data:
            api_key = data['groq_api_key'].strip()
            if api_key:
                encrypted = encrypt_api_key(api_key)
                set_setting('groq_api_key', encrypted, 'Encrypted Groq API Key')
        
        if 'openai_api_key' in data:
            api_key = data['openai_api_key'].strip()
            if api_key:
                encrypted = encrypt_api_key(api_key)
                set_setting('openai_api_key', encrypted, 'Encrypted OpenAI API Key')
        
        # Update default models
        if 'groq_default_model' in data:
            model = data['groq_default_model'].strip()
            set_setting('groq_default_model', model, 'Default Groq model')
        
        if 'openai_default_model' in data:
            model = data['openai_default_model'].strip()
            set_setting('openai_default_model', model, 'Default OpenAI model')
        
        # Update user model selection flags
        if 'groq_allow_user_model_selection' in data:
            allow = 'true' if data['groq_allow_user_model_selection'] else 'false'
            set_setting('groq_allow_user_model_selection', allow, 'Allow users to select Groq model')
        
        if 'openai_allow_user_model_selection' in data:
            allow = 'true' if data['openai_allow_user_model_selection'] else 'false'
            set_setting('openai_allow_user_model_selection', allow, 'Allow users to select OpenAI model')
        
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'LLM settings updated successfully'
        })
    except Exception as e:
        logger.error(f"Error updating LLM settings: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== USER MODEL SELECTION ====================

@bp.route('/settings/user-model', methods=['GET'])
@login_required
def get_user_model():
    """Get user's selected model (if allowed)"""
    try:
        from app.utils.db import get_db
        from app.models.database_models import SystemSettings, UserSettings
        from app.utils.llm_models import GROQ_MODELS, OPENAI_MODELS
        
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401
        
        db = get_db()
        
        # Get active provider
        provider_setting = db.query(SystemSettings).filter(
            SystemSettings.key == 'active_provider'
        ).first()
        active_provider = (provider_setting.value.upper() if provider_setting else 'OPENAI')
        
        # Check if user selection is allowed
        allow_setting = db.query(SystemSettings).filter(
            SystemSettings.key == f'{active_provider.lower()}_allow_user_model_selection'
        ).first()
        
        allow_user_selection = (
            allow_setting and 
            allow_setting.value.lower() == 'true'
        )
        
        # Get user's selected model
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == user_id
        ).first()
        
        selected_model = user_settings.selected_model if user_settings else None
        
        # Get available models for current provider
        if active_provider == 'GROQ':
            available_models = {
                'qwen': GROQ_MODELS['qwen'],
                'llama': GROQ_MODELS['llama']
            }
        else:
            available_models = OPENAI_MODELS
        
        return jsonify({
            'success': True,
            'allow_user_selection': allow_user_selection,
            'selected_model': selected_model,
            'active_provider': active_provider,
            'available_models': available_models
        })
    except Exception as e:
        logger.error(f"Error getting user model: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/settings/user-model', methods=['PUT'])
@login_required
def set_user_model():
    """Set user's selected model (if allowed)"""
    try:
        from app.utils.db import get_db
        from app.models.database_models import SystemSettings, UserSettings
        from app.utils.llm_models import is_valid_model_for_provider
        
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401
        
        data = request.json
        model_id = data.get('model_id', '').strip()
        
        if not model_id:
            return jsonify({'success': False, 'error': 'model_id is required'}), 400
        
        db = get_db()
        
        # Get active provider
        provider_setting = db.query(SystemSettings).filter(
            SystemSettings.key == 'active_provider'
        ).first()
        active_provider = (provider_setting.value.upper() if provider_setting else 'OPENAI')
        
        # Check if user selection is allowed
        allow_setting = db.query(SystemSettings).filter(
            SystemSettings.key == f'{active_provider.lower()}_allow_user_model_selection'
        ).first()
        
        allow_user_selection = (
            allow_setting and 
            allow_setting.value.lower() == 'true'
        )
        
        if not allow_user_selection:
            return jsonify({
                'success': False, 
                'error': 'User model selection is not enabled for the current provider'
            }), 403
        
        # Validate model
        if not is_valid_model_for_provider(model_id, active_provider):
            return jsonify({
                'success': False,
                'error': f'Invalid model "{model_id}" for provider {active_provider}'
            }), 400
        
        # Update or create user settings
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == user_id
        ).first()
        
        if user_settings:
            user_settings.selected_model = model_id
            user_settings.updated_at = datetime.utcnow()
        else:
            user_settings = UserSettings(
                user_id=user_id,
                selected_model=model_id
            )
            db.add(user_settings)
        
        db.commit()
        
        return jsonify({
            'success': True,
            'message': f'Model set to {model_id}',
            'selected_model': model_id
        })
    except Exception as e:
        logger.error(f"Error setting user model: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

