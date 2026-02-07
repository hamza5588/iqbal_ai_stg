from flask import Blueprint, request, jsonify, current_app, session, send_file
from werkzeug.utils import secure_filename
from io import BytesIO
import os
import logging
import tempfile
import json

from app.models import VectorStoreModel, UserModel
from app.services.lesson_service import LessonService
from app.utils.constants import MAX_FILE_SIZE

logger = logging.getLogger(__name__)
bp = Blueprint('files', __name__)

@bp.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload and lesson generation"""
    if 'user_id' not in session:
        logger.warning("Unauthorized upload attempt")
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        # Check if files are present
        if 'files' not in request.files:
            logger.warning("No files in request")
            return jsonify({'error': 'No files provided'}), 400

        files = request.files.getlist('files')
        if not files or all(file.filename == '' for file in files):
            logger.warning("Empty file list")
            return jsonify({'error': 'No selected files'}), 400

        # For now, process only the first file (you can extend this later)
        file = files[0]
        
        # Validate file type
        if not file.filename.lower().endswith(('.pdf', '.doc', '.docx', '.txt')):
            logger.warning(f"Unsupported file type: {file.filename}")
            return jsonify({'error': 'Only PDF, Word, and text files are supported'}), 400

        # Initialize LessonService with API key from session
        api_key = session.get('groq_api_key')
        if not api_key:
            return jsonify({'error': 'API key not found. Please set your API key first.'}), 400
            
        lesson_service = LessonService(api_key=api_key)
        
        # Process the file and generate lesson
        result = lesson_service.process_file(file)
        
        if 'error' in result:
            logger.error(f"Lesson generation failed: {result['error']}")
            return jsonify({
                'error': result['error'], 
                'details': result.get('details', '')
            }), 500
        
        # Store the generated DOCX bytes in session for download
        session['last_generated_docx'] = result['docx_bytes']
        session['last_generated_filename'] = result['filename']
        session.modified = True
        
        return jsonify({
            'success': True,
            'message': 'Lesson generated successfully',
            'lesson': result['lesson'],
            'filename': result['filename'],
            'download_ready': True
        })

    except Exception as e:
        logger.error(f"Upload failed: {str(e)}", exc_info=True)
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@bp.route('/generate_lesson', methods=['POST'])
def generate_lesson():
    """Endpoint for lesson generation that returns JSON with lesson data."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
        
        # Get form data for lesson configuration
        lesson_title = request.form.get('lessonTitle', '')
        learning_objective = request.form.get('learningObjective', '')
        focus_area = request.form.get('focusArea', '')
        grade_level = request.form.get('gradeLevel', '')
        additional_notes = request.form.get('additionalNotes', '')
        
        allowed_extensions = {'.pdf', '.doc', '.docx', '.txt'}
        file_ext = os.path.splitext(file.filename.lower())[1]
        if file_ext not in allowed_extensions:
            return jsonify({'error': 'File type not supported. Please upload PDF, DOC, DOCX, or TXT files.'}), 400
        
        api_key = session.get('groq_api_key')
        if not api_key:
            return jsonify({'error': 'API key not configured. Please set your API key first.'}), 400
        
        lesson_service = LessonService(api_key=api_key)
        
        # Create lesson details from form data
        lesson_details = {
            'title': lesson_title,
            'learning_objective': learning_objective,
            'focus_area': focus_area,
            'grade_level': grade_level,
            'additional_notes': additional_notes
        }
        
        result = lesson_service.process_file(file, lesson_details)
        
        if 'error' in result:
            return jsonify({
                'error': result['error'],
                'details': result.get('details', '')
            }), 500
        
        # Store the generated lesson data in session for later download
        session['last_generated_lesson'] = result['lesson']
        session['last_generated_docx'] = result['docx_bytes']
        session['last_generated_filename'] = result['filename']
        session.modified = True
        
        # Return JSON response with lesson data
        return jsonify({
            'success': True,
            'lesson': result['lesson'],
            'filename': result['filename'],
            'message': 'Lesson generated successfully!'
        })
        
    except Exception as e:
        logger.error(f"Lesson generation error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to generate lesson: {str(e)}'}), 500
    
@bp.route('/download_lesson', methods=['POST'])
def download_lesson():
    """Download the lesson as DOCX file with updated content"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        lesson_data = data.get('lesson_data')
        
        if not lesson_data:
            return jsonify({'error': 'No lesson data provided'}), 400
        
        # Create lesson structure from the provided data
        lesson = {
            'title': lesson_data.get('title', 'Lesson'),
            'summary': lesson_data.get('objective', ''),
            'learning_objectives': [lesson_data.get('objective', '')],
            'sections': [{'heading': 'Lesson Content', 'content': lesson_data.get('content', '')}],
            'key_concepts': [],
            'activities': [],
            'quiz': []
        }
        
        # Get API key from session
        api_key = session.get('groq_api_key')
        if not api_key:
            return jsonify({'error': 'API key not configured. Please set your API key first.'}), 400
        
        # Generate DOCX from the lesson data
        lesson_service = LessonService(api_key=api_key)
        docx_bytes = lesson_service._create_docx(lesson)
        
        # Create filename
        filename = lesson_data.get('title', 'lesson').replace(' ', '_') + '.docx'
        
        # Create BytesIO object from generated bytes
        docx_buffer = BytesIO(docx_bytes)
        docx_buffer.seek(0)
        
        return send_file(
            docx_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        
    except Exception as e:
        logger.error(f"Download error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to download lesson: {str(e)}'}), 500

@bp.route('/update_api_key', methods=['POST'])
def update_api_key():
    """Update the user's API key"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        data = request.get_json(silent=True) or {}
        new_api_key = data.get('api_key', '').strip()
        
        if not new_api_key:
            return jsonify({'error': 'API key is required'}), 400

        # Test the API key first
        try:
            test_service = LessonService(api_key=new_api_key)
            # You might want to add a simple test here
        except Exception as e:
            logger.error(f"Invalid API key: {str(e)}")
            return jsonify({'error': 'Invalid API key provided'}), 400

        # Update in database
        user_model = UserModel(session['user_id'])
        user_model.update_api_key(new_api_key)
        
        # Update session
        session['groq_api_key'] = new_api_key
        session.modified = True

        return jsonify({
            'success': True,
            'message': 'API key updated successfully'
        })

    except Exception as e:
        logger.error(f"API key update error: {str(e)}")
        return jsonify({'error': 'Failed to update API key'}), 500

@bp.route('/edit_lesson_with_prompt', methods=['POST'])
def edit_lesson_with_prompt():
    """Edit the lesson markdown using an AI prompt."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    try:
        data = request.get_json()
        lesson_text = data.get('lesson_text')
        user_prompt = data.get('user_prompt')
        filename = data.get('filename', '')  # Get filename for RAG lookup
        
        if not lesson_text or not user_prompt:
            return jsonify({'error': 'Missing lesson_text or user_prompt'}), 400
        api_key = session.get('groq_api_key')
        if not api_key:
            return jsonify({'error': 'API key not configured. Please set your API key first.'}), 400
        lesson_service = LessonService(api_key=api_key)
        new_markdown = lesson_service.edit_lesson_with_prompt(lesson_text, user_prompt, filename)
        return jsonify({'lesson_markdown': new_markdown})
    except Exception as e:
        logger.error(f"Edit lesson with prompt error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to edit lesson: {str(e)}'}), 500

@bp.route('/download_lesson_ppt', methods=['POST'])
def download_lesson_ppt():
    """Download the lesson as PPTX from posted lesson JSON."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    try:
        data = request.get_json()
        lesson = data.get('lesson')
        if not lesson:
            return jsonify({'error': 'No lesson data provided'}), 400
        # Get API key from session
        api_key = session.get('groq_api_key')
        if not api_key:
            return jsonify({'error': 'API key not configured. Please set your API key first.'}), 400
        
        lesson_service = LessonService(api_key=api_key)
        ppt_bytes = lesson_service.create_ppt(lesson)
        filename = lesson.get('title', 'lesson').replace(' ', '_') + '.pptx'
        ppt_buffer = BytesIO(ppt_bytes)
        ppt_buffer.seek(0)
        response = send_file(
            ppt_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
        )
        return response
    except Exception as e:
        logger.error(f"Download lesson ppt error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to download lesson ppt: {str(e)}'}), 500

@bp.route('/download_edited_lesson', methods=['POST'])
def download_edited_lesson():
    """Download an edited lesson as DOCX from posted lesson JSON or markdown."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    try:
        data = request.get_json()
        lesson = data.get('lesson')
        lesson_markdown = data.get('lesson_markdown')
        # Get API key from session
        api_key = session.get('groq_api_key')
        if not api_key:
            return jsonify({'error': 'API key not configured. Please set your API key first.'}), 400
        
        lesson_service = LessonService(api_key=api_key)
        if lesson_markdown:
            # Convert markdown to lesson structure (simple fallback: put all in one section)
            lesson = {
                'title': 'Lesson',
                'summary': '',
                'learning_objectives': [],
                'sections': [{'heading': 'Lesson', 'content': lesson_markdown}],
                'key_concepts': [],
                'activities': [],
                'quiz': []
            }
        if not lesson:
            return jsonify({'error': 'No lesson data provided'}), 400
        docx_bytes = lesson_service._create_docx(lesson)
        filename = lesson.get('title', 'lesson_edited').replace(' ', '_') + '.docx'
        docx_buffer = BytesIO(docx_bytes)
        docx_buffer.seek(0)
        response = send_file(
            docx_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        return response
    except Exception as e:
        logger.error(f"Download edited lesson error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to download edited lesson: {str(e)}'}), 500
