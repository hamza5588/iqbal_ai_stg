from flask import Blueprint, request, jsonify, session
from app.models import UserModel
from app.models.database_models import User as DBUser, SystemSettings
from app.services import ChatService
from app.utils.db import get_db
import logging

logger = logging.getLogger(__name__)
bp = Blueprint('api_key', __name__)

def get_llm_provider():
    """Get current LLM provider from system settings"""
    try:
        db = get_db()
        setting = db.query(SystemSettings).filter(SystemSettings.key == 'llm_provider').first()
        return setting.value if setting else 'openai'
    except Exception as e:
        logger.error(f"Error getting LLM provider: {str(e)}")
        return 'openai'

@bp.route('/update', methods=['POST'])
@bp.route('/update_api_key', methods=['POST'])
def update_api_key():
    """Update user's API key (works for both OpenAI and Groq depending on system setting)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        data = request.json
        new_api_key = data.get('api_key')
        
        if not new_api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400

        # Get current provider
        provider = get_llm_provider()
        
        # For Groq, we can test the API key
        # For OpenAI, we'll just save it (validation happens on first use)
        if provider == 'groq':
            try:
                # Test the API key by creating a simple LLM instance
                from app.utils.llm_factory import create_llm
                test_llm = create_llm(api_key=new_api_key, provider='groq')
                # Try a simple invoke to test
                test_llm.invoke("test")
            except Exception as e:
                logger.error(f"Invalid Groq API key: {str(e)}")
                return jsonify({'success': False, 'error': 'Invalid Groq API key. Please check your key and try again.'}), 400

        # Update in database
        user_model = UserModel(session['user_id'])
        user_model.update_api_key(new_api_key)
        
        # Update session with new API key
        session.pop('groq_api_key', None)  # Remove old key
        session['groq_api_key'] = new_api_key  # Set new key
        session.modified = True  # Mark session as modified
        
        # Verify the update by checking the database directly
        db = get_db()
        updated_user = db.query(DBUser).filter(DBUser.id == session['user_id']).first()
        
        if not updated_user or updated_user.groq_api_key != new_api_key:
            logger.error("Failed to verify API key update in database")
            return jsonify({'success': False, 'error': 'Failed to verify API key update'}), 500

        return jsonify({
            'success': True,
            'message': 'API key updated successfully'
        })

    except Exception as e:
        logger.error(f"API key update error: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to update API key'}), 500

@bp.route('/status', methods=['GET'])
@bp.route('/check_api_key_status', methods=['GET'])
def check_api_key_status():
    """Check API key status and current LLM provider"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        # Get current LLM provider
        provider = get_llm_provider()
        
        # Check if API key exists in database
        user = UserModel.get_user_by_id(session['user_id'])
        has_api_key = bool(user and user.get('groq_api_key'))
        
        return jsonify({
            'success': True,
            'has_api_key': has_api_key,
            'provider': provider
        })
        
    except Exception as e:
        logger.error(f"API key status check error: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to check API key status'}), 500 