"""
Admin routes for system management
Provides comprehensive admin functionality including user management,
RAG prompt management, coupon management, and lesson management.
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
    SystemSettings,
    UserSettings,
    LLMUsageEvent,
    LLMModelPricing,
)
from app.utils.rag_service import (
    DEFAULT_RAG_CHAT_SYSTEM_BODY_NO_PDF,
    DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF,
    RAG_SYSTEM_SETTING_KEY_NO_PDF,
    RAG_SYSTEM_SETTING_KEY_WITH_PDF,
)
from sqlalchemy import or_, func, desc, cast, Date
from datetime import datetime, timedelta
from decimal import Decimal
import logging
import os

logger = logging.getLogger(__name__)
bp = Blueprint('admin', __name__, url_prefix='/admin')


def _estimate_tokens_rough(text: str) -> int:
    """Conservative token estimate (~4 characters per token) for admin prompt limits."""
    if not text:
        return 0
    return max(0, len(text) // 4)


def get_rag_admin_system_prompt_limits() -> dict:
    """
    Token budget for admin-edited RAG system bodies.

    Groq model docs (e.g. qwen/qwen3-32b) list CONTEXT WINDOW 131,072 — same model limit on free vs paid;
    paid tiers mainly increase throughput (TPM/RPM), not context size.

    We reserve space for: completion, multi-turn history + tool messages, user custom RAG prompt (~300 words),
    and misc overhead so admin prompts cannot consume the full window.
    """
    context = int(os.getenv("GROQ_MODEL_CONTEXT_WINDOW_TOKENS", "131072"))
    reserved_out = int(os.getenv("RAG_RESERVED_OUTPUT_TOKENS", "8192"))
    reserved_history = int(os.getenv("RAG_RESERVED_HISTORY_TOKENS", "52000"))
    reserved_user_custom = int(os.getenv("RAG_RESERVED_USER_RAG_PROMPT_TOKENS", "800"))
    reserved_misc = int(os.getenv("RAG_RESERVED_MISC_TOKENS", "8192"))
    max_body = context - reserved_out - reserved_history - reserved_user_custom - reserved_misc
    max_body = max(4096, int(max_body))
    return {
        "context_window_tokens": context,
        "reserved_output_tokens": reserved_out,
        "reserved_history_tokens": reserved_history,
        "reserved_user_custom_rag_prompt_tokens": reserved_user_custom,
        "reserved_misc_tokens": reserved_misc,
        "max_system_body_tokens_estimated": max_body,
        "doc_note": (
            "Based on Groq model context limits (e.g. Qwen 3 32B: 131,072 tokens — "
            "see console.groq.com/docs/model/qwen3-32b). Paid plan increases rate limits, not context window."
        ),
    }


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


@bp.route("/school-operations")
@login_required
@admin_only
def school_operations():
    """Dark-theme school org console (schools, rosters, publish/quiz API checks). Admin-family roles only."""
    return render_template("admin/school_operations.html")


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


# ==================== RAG / PDF CHAT SYSTEM PROMPT (system_settings) ====================

def _upsert_or_delete_system_text(db, key: str, value: str, user_id: int, description: str = None):
    """Persist non-empty text; delete row if value is empty (revert to app default)."""
    row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
    cleaned = (value or "").strip()
    if not cleaned:
        if row:
            db.delete(row)
        return
    if row:
        row.value = cleaned
        row.updated_at = datetime.utcnow()
        row.updated_by = user_id
        if description is not None:
            row.description = description
    else:
        db.add(
            SystemSettings(
                key=key,
                value=cleaned,
                description=description,
                updated_by=user_id,
            )
        )


@bp.route('/prompt/rag', methods=['GET'])
@login_required
@admin_only
def get_rag_system_prompt():
    """Get RAG chat system prompt bodies (stored or built-in defaults for display)."""
    try:
        db = get_db()
        row_pdf = db.query(SystemSettings).filter(SystemSettings.key == RAG_SYSTEM_SETTING_KEY_WITH_PDF).first()
        row_no = db.query(SystemSettings).filter(SystemSettings.key == RAG_SYSTEM_SETTING_KEY_NO_PDF).first()

        prompt_with_pdf = (
            row_pdf.value if row_pdf and row_pdf.value.strip() else DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF
        )
        prompt_no_pdf = (
            row_no.value if row_no and row_no.value.strip() else DEFAULT_RAG_CHAT_SYSTEM_BODY_NO_PDF
        )
        updated_at = None
        for r in (row_pdf, row_no):
            if r and r.updated_at:
                if updated_at is None or r.updated_at > updated_at:
                    updated_at = r.updated_at

        return jsonify({
            'success': True,
            'prompt_with_pdf': prompt_with_pdf,
            'prompt_no_pdf': prompt_no_pdf,
            'using_defaults_with_pdf': row_pdf is None or not (row_pdf.value or '').strip(),
            'using_defaults_no_pdf': row_no is None or not (row_no.value or '').strip(),
            'updated_at': updated_at.isoformat() if updated_at else None,
            'limits': get_rag_admin_system_prompt_limits(),
            'estimated_tokens': {
                'with_pdf': _estimate_tokens_rough(prompt_with_pdf),
                'no_pdf': _estimate_tokens_rough(prompt_no_pdf),
            },
        })
    except Exception as e:
        logger.error(f"Error getting RAG system prompt: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/prompt/rag', methods=['POST'])
@login_required
@admin_only
def set_rag_system_prompt():
    """Save RAG chat system prompts. Empty string for a field removes override (uses code default)."""
    try:
        data = request.get_json(silent=True) or {}
        prompt_with_pdf = data.get('prompt_with_pdf', '')
        prompt_no_pdf = data.get('prompt_no_pdf', '')

        limits = get_rag_admin_system_prompt_limits()
        max_tok = limits["max_system_body_tokens_estimated"]

        checks = [
            ("prompt_with_pdf", prompt_with_pdf),
            ("prompt_no_pdf", prompt_no_pdf),
        ]
        for field_name, text in checks:
            if not (text or "").strip():
                continue
            est = _estimate_tokens_rough(text)
            if est > max_tok:
                return jsonify({
                    'success': False,
                    'error': (
                        f'{field_name} is too long: about {est} tokens estimated (~4 chars/token), '
                        f'max allowed {max_tok} tokens for the admin RAG system body. '
                        f'Shorten the text so the model still has room for chat history, tools, and output '
                        f'(context window {limits["context_window_tokens"]} tokens; see limits in GET response).'
                    ),
                    'code': 'RAG_PROMPT_TOO_LONG',
                    'field': field_name,
                    'estimated_tokens': est,
                    'max_system_body_tokens_estimated': max_tok,
                    'limits': limits,
                }), 400

        db = get_db()
        _upsert_or_delete_system_text(
            db,
            RAG_SYSTEM_SETTING_KEY_WITH_PDF,
            prompt_with_pdf,
            session['user_id'],
            description='RAG chat system message when a PDF is uploaded ({filename}, {page_info}, {thread_id})',
        )
        _upsert_or_delete_system_text(
            db,
            RAG_SYSTEM_SETTING_KEY_NO_PDF,
            prompt_no_pdf,
            session['user_id'],
            description='RAG chat system message when no PDF is uploaded yet',
        )
        db.commit()

        return jsonify({'success': True, 'message': 'RAG system prompts updated'})
    except Exception as e:
        logger.error(f"Error saving RAG system prompt: {str(e)}")
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/prompt/rag', methods=['DELETE'])
@login_required
@admin_only
def delete_rag_system_prompt():
    """Remove stored RAG prompts so built-in defaults are used."""
    try:
        db = get_db()
        for key in (RAG_SYSTEM_SETTING_KEY_WITH_PDF, RAG_SYSTEM_SETTING_KEY_NO_PDF):
            row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
            if row:
                db.delete(row)
        db.commit()
        return jsonify({'success': True, 'message': 'RAG system prompts reset to defaults'})
    except Exception as e:
        logger.error(f"Error deleting RAG system prompt: {str(e)}")
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


@bp.route('/load-testing', methods=['GET'])
@login_required
@admin_only
def load_testing_dashboard():
    """Load testing dashboard"""
    return render_template('admin/load_testing.html')


# ==================== STRESS TEST MODE TOGGLE ====================

STRESS_TEST_SETTING_KEY = 'stress_test_mode'


@bp.route('/stress-test-mode', methods=['GET'])
@login_required
@admin_only
def get_stress_test_mode():
    """Return the current stress test mode state."""
    try:
        db = get_db()
        row = db.query(SystemSettings).filter(SystemSettings.key == STRESS_TEST_SETTING_KEY).first()
        enabled = (row.value.lower() == 'true') if row else False
        return jsonify({'success': True, 'stress_test_mode': enabled})
    except Exception as e:
        logger.error(f"Error getting stress_test_mode: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/stress-test-mode', methods=['POST'])
@login_required
@admin_only
def set_stress_test_mode():
    """Enable or disable stress test mode (applies max_tokens caps to LLM calls)."""
    try:
        data = request.get_json() or {}
        enabled = bool(data.get('enabled', False))
        db = get_db()
        row = db.query(SystemSettings).filter(SystemSettings.key == STRESS_TEST_SETTING_KEY).first()
        if row:
            row.value = 'true' if enabled else 'false'
            row.updated_by = session.get('user_id')
        else:
            db.add(SystemSettings(
                key=STRESS_TEST_SETTING_KEY,
                value='true' if enabled else 'false',
                description='When enabled, applies max_tokens caps to LLM calls to reduce Groq token usage during stress tests.',
                updated_by=session.get('user_id'),
            ))
        db.commit()
        logger.info("Stress test mode set to %s by user %s", enabled, session.get('user_id'))
        return jsonify({'success': True, 'stress_test_mode': enabled})
    except Exception as e:
        logger.error(f"Error setting stress_test_mode: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== LLM TELEMETRY ====================


@bp.route('/llm-telemetry', methods=['GET'])
@login_required
@admin_only
def llm_telemetry_page():
    """LLM usage, cost, and latency analytics."""
    return render_template('admin/llm_telemetry.html')


@bp.route('/llm-telemetry/api/summary', methods=['GET'])
@login_required
@admin_only
def llm_telemetry_api_summary():
    """Aggregated metrics for charts and cards."""
    try:
        db = get_db()
        days = max(1, min(90, int(request.args.get('days', 7))))
        traffic = (request.args.get('traffic_source') or 'production').strip().lower()
        workflow_filter = (request.args.get('workflow') or '').strip() or None
        provider_filter = (request.args.get('provider') or '').strip() or None

        since = datetime.utcnow() - timedelta(days=days)
        q = db.query(LLMUsageEvent).filter(LLMUsageEvent.created_at >= since)
        if traffic == 'production':
            q = q.filter(LLMUsageEvent.traffic_source == 'production')
        elif traffic == 'load_test':
            q = q.filter(LLMUsageEvent.traffic_source == 'load_test')
        if workflow_filter:
            q = q.filter(LLMUsageEvent.workflow == workflow_filter)
        if provider_filter:
            q = q.filter(LLMUsageEvent.provider == provider_filter)

        total_requests = q.count()
        err_q = q.filter(LLMUsageEvent.success.is_(False))
        error_count = err_q.count()

        sum_cost = db.query(func.coalesce(func.sum(LLMUsageEvent.cost_usd), 0)).filter(
            LLMUsageEvent.created_at >= since,
        )
        sum_tokens = db.query(func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0)).filter(
            LLMUsageEvent.created_at >= since,
        )
        if traffic == 'production':
            sum_cost = sum_cost.filter(LLMUsageEvent.traffic_source == 'production')
            sum_tokens = sum_tokens.filter(LLMUsageEvent.traffic_source == 'production')
        elif traffic == 'load_test':
            sum_cost = sum_cost.filter(LLMUsageEvent.traffic_source == 'load_test')
            sum_tokens = sum_tokens.filter(LLMUsageEvent.traffic_source == 'load_test')
        if workflow_filter:
            sum_cost = sum_cost.filter(LLMUsageEvent.workflow == workflow_filter)
            sum_tokens = sum_tokens.filter(LLMUsageEvent.workflow == workflow_filter)
        if provider_filter:
            sum_cost = sum_cost.filter(LLMUsageEvent.provider == provider_filter)
            sum_tokens = sum_tokens.filter(LLMUsageEvent.provider == provider_filter)

        total_cost = float(sum_cost.scalar() or 0)
        total_token_sum = int(sum_tokens.scalar() or 0)

        by_workflow = (
            db.query(
                LLMUsageEvent.workflow,
                func.count(LLMUsageEvent.id),
                func.coalesce(func.sum(LLMUsageEvent.cost_usd), 0),
            )
            .filter(LLMUsageEvent.created_at >= since)
        )
        if traffic == 'production':
            by_workflow = by_workflow.filter(LLMUsageEvent.traffic_source == 'production')
        elif traffic == 'load_test':
            by_workflow = by_workflow.filter(LLMUsageEvent.traffic_source == 'load_test')
        if workflow_filter:
            by_workflow = by_workflow.filter(LLMUsageEvent.workflow == workflow_filter)
        if provider_filter:
            by_workflow = by_workflow.filter(LLMUsageEvent.provider == provider_filter)
        by_workflow = (
            by_workflow.group_by(LLMUsageEvent.workflow)
            .order_by(desc(func.count(LLMUsageEvent.id)))
            .all()
        )

        by_provider_model = (
            db.query(
                LLMUsageEvent.provider,
                LLMUsageEvent.model,
                func.count(LLMUsageEvent.id),
                func.coalesce(func.sum(LLMUsageEvent.cost_usd), 0),
            )
            .filter(LLMUsageEvent.created_at >= since)
        )
        if traffic == 'production':
            by_provider_model = by_provider_model.filter(LLMUsageEvent.traffic_source == 'production')
        elif traffic == 'load_test':
            by_provider_model = by_provider_model.filter(LLMUsageEvent.traffic_source == 'load_test')
        if workflow_filter:
            by_provider_model = by_provider_model.filter(LLMUsageEvent.workflow == workflow_filter)
        if provider_filter:
            by_provider_model = by_provider_model.filter(LLMUsageEvent.provider == provider_filter)
        by_provider_model = (
            by_provider_model.group_by(LLMUsageEvent.provider, LLMUsageEvent.model)
            .order_by(desc(func.count(LLMUsageEvent.id)))
            .limit(50)
            .all()
        )

        by_day = (
            db.query(
                cast(LLMUsageEvent.created_at, Date),
                func.count(LLMUsageEvent.id),
                func.coalesce(func.sum(LLMUsageEvent.cost_usd), 0),
            )
            .filter(LLMUsageEvent.created_at >= since)
        )
        if traffic == 'production':
            by_day = by_day.filter(LLMUsageEvent.traffic_source == 'production')
        elif traffic == 'load_test':
            by_day = by_day.filter(LLMUsageEvent.traffic_source == 'load_test')
        if workflow_filter:
            by_day = by_day.filter(LLMUsageEvent.workflow == workflow_filter)
        if provider_filter:
            by_day = by_day.filter(LLMUsageEvent.provider == provider_filter)
        by_day = by_day.group_by(cast(LLMUsageEvent.created_at, Date)).order_by(cast(LLMUsageEvent.created_at, Date)).all()

        top_users = (
            db.query(
                LLMUsageEvent.user_id,
                func.count(LLMUsageEvent.id).label('n'),
                func.coalesce(func.sum(LLMUsageEvent.cost_usd), 0).label('c'),
            )
            .filter(LLMUsageEvent.created_at >= since)
            .filter(LLMUsageEvent.user_id.isnot(None))
        )
        if traffic == 'production':
            top_users = top_users.filter(LLMUsageEvent.traffic_source == 'production')
        elif traffic == 'load_test':
            top_users = top_users.filter(LLMUsageEvent.traffic_source == 'load_test')
        if workflow_filter:
            top_users = top_users.filter(LLMUsageEvent.workflow == workflow_filter)
        if provider_filter:
            top_users = top_users.filter(LLMUsageEvent.provider == provider_filter)
        top_users = top_users.group_by(LLMUsageEvent.user_id).order_by(desc('n')).limit(15).all()

        latency_avg = db.query(func.avg(LLMUsageEvent.duration_ms)).filter(
            LLMUsageEvent.created_at >= since,
            LLMUsageEvent.success.is_(True),
        )
        if traffic == 'production':
            latency_avg = latency_avg.filter(LLMUsageEvent.traffic_source == 'production')
        elif traffic == 'load_test':
            latency_avg = latency_avg.filter(LLMUsageEvent.traffic_source == 'load_test')
        if workflow_filter:
            latency_avg = latency_avg.filter(LLMUsageEvent.workflow == workflow_filter)
        if provider_filter:
            latency_avg = latency_avg.filter(LLMUsageEvent.provider == provider_filter)
        avg_ms = float(latency_avg.scalar() or 0)

        return jsonify({
            'success': True,
            'period_days': days,
            'traffic_source': traffic,
            'totals': {
                'requests': total_requests,
                'errors': error_count,
                'total_cost_usd': total_cost,
                'total_tokens': total_token_sum,
                'avg_latency_ms_success': avg_ms,
            },
            'by_workflow': [
                {'workflow': w, 'requests': int(n), 'cost_usd': float(c or 0)}
                for w, n, c in by_workflow
            ],
            'by_provider_model': [
                {'provider': p, 'model': m, 'requests': int(n), 'cost_usd': float(c or 0)}
                for p, m, n, c in by_provider_model
            ],
            'by_day': [
                {'day': str(d), 'requests': int(n), 'cost_usd': float(c or 0)}
                for d, n, c in by_day
            ],
            'top_users': [
                {'user_id': uid, 'requests': int(n), 'cost_usd': float(c or 0)}
                for uid, n, c in top_users
            ],
        })
    except Exception as e:
        logger.error(f"llm_telemetry_api_summary: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/llm-telemetry/api/pricing', methods=['GET', 'POST'])
@login_required
@admin_only
def llm_telemetry_api_pricing():
    """List or upsert per-model pricing (USD per 1M tokens)."""
    db = get_db()
    if request.method == 'GET':
        try:
            rows = db.query(LLMModelPricing).order_by(LLMModelPricing.provider, LLMModelPricing.model).all()
            return jsonify({
                'success': True,
                'pricing': [
                    {
                        'id': r.id,
                        'provider': r.provider,
                        'model': r.model,
                        'input_usd_per_million': float(r.input_usd_per_million or 0),
                        'output_usd_per_million': float(r.output_usd_per_million or 0),
                        'updated_at': r.updated_at.isoformat() if r.updated_at else None,
                    }
                    for r in rows
                ],
            })
        except Exception as e:
            logger.error(f"llm pricing get: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    data = request.get_json() or {}
    provider = (data.get('provider') or '').strip().lower()
    model = (data.get('model') or '').strip()
    try:
        inp = Decimal(str(data.get('input_usd_per_million', 0)))
        out = Decimal(str(data.get('output_usd_per_million', 0)))
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid numeric pricing'}), 400
    if not provider or not model:
        return jsonify({'success': False, 'error': 'provider and model are required'}), 400
    try:
        row = (
            db.query(LLMModelPricing)
            .filter(LLMModelPricing.provider == provider, LLMModelPricing.model == model)
            .first()
        )
        if row:
            row.input_usd_per_million = inp
            row.output_usd_per_million = out
            row.updated_at = datetime.utcnow()
        else:
            row = LLMModelPricing(
                provider=provider,
                model=model,
                input_usd_per_million=inp,
                output_usd_per_million=out,
            )
            db.add(row)
        db.commit()
        return jsonify({'success': True, 'id': row.id})
    except Exception as e:
        db.rollback()
        logger.error(f"llm pricing post: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/llm-telemetry/api/export.csv', methods=['GET'])
@login_required
@admin_only
def llm_telemetry_export_csv():
    """Export raw events for the selected window (cap rows)."""
    import csv
    from io import StringIO

    try:
        db = get_db()
        days = max(1, min(90, int(request.args.get('days', 7))))
        traffic = (request.args.get('traffic_source') or 'production').strip().lower()
        since = datetime.utcnow() - timedelta(days=days)
        q = db.query(LLMUsageEvent).filter(LLMUsageEvent.created_at >= since).order_by(LLMUsageEvent.created_at.desc())
        if traffic == 'production':
            q = q.filter(LLMUsageEvent.traffic_source == 'production')
        elif traffic == 'load_test':
            q = q.filter(LLMUsageEvent.traffic_source == 'load_test')
        rows = q.limit(50000).all()

        buf = StringIO()
        w = csv.writer(buf)
        w.writerow([
            'created_at', 'user_id', 'user_role', 'traffic_source', 'workflow', 'provider', 'model',
            'input_tokens', 'output_tokens', 'total_tokens', 'cost_usd', 'duration_ms', 'success',
            'error_class', 'conversation_id', 'thread_id', 'celery_task_name',
        ])
        for r in rows:
            w.writerow([
                r.created_at.isoformat() if r.created_at else '',
                r.user_id or '',
                r.user_role or '',
                r.traffic_source or '',
                r.workflow or '',
                r.provider or '',
                r.model or '',
                r.input_tokens if r.input_tokens is not None else '',
                r.output_tokens if r.output_tokens is not None else '',
                r.total_tokens if r.total_tokens is not None else '',
                float(r.cost_usd) if r.cost_usd is not None else '',
                r.duration_ms,
                r.success,
                r.error_class or '',
                r.conversation_id or '',
                r.thread_id or '',
                r.celery_task_name or '',
            ])
        from flask import Response

        return Response(
            buf.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=llm_usage_events.csv'},
        )
    except Exception as e:
        logger.error(f"llm export: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

