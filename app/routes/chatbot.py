from flask import Blueprint, request, jsonify, session
from app.utils.auth import login_required
from app.utils.rag_service import (
    ingest_pdf,
    chatbot,
    thread_has_document,
    thread_document_metadata
)
from langchain_core.messages import HumanMessage
import logging

logger = logging.getLogger(__name__)
bp = Blueprint('rag', __name__)


def _get_thread_id(user_id: int, conversation_id: int = None) -> str:
    """
    Generate a thread_id for the RAG service.
    Uses user_id as base, optionally combined with conversation_id.
    """
    if conversation_id:
        return f"user_{user_id}_conv_{conversation_id}"
    return f"user_{user_id}_default"


def _validate_thread_id(thread_id: str, user_id: int) -> bool:
    """
    Validate that a thread_id belongs to the current user.
    Prevents users from accessing other users' threads.
    """
    if not thread_id:
        return False
    
    # Thread ID must start with the user's ID
    expected_prefix = f"user_{user_id}_"
    return thread_id.startswith(expected_prefix)


@bp.route('/ingest', methods=['POST'])
@login_required
def ingest():
    """
    Upload and ingest a PDF document for RAG.
    Expects a file in the 'file' field of the request.
    Optionally accepts 'thread_id' or 'conversation_id' in form data.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        # Check if file is present
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Validate file type
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'error': 'Only PDF files are supported'}), 400

        # Get thread_id from request or generate default
        conversation_id = request.form.get('conversation_id', type=int)
        provided_thread_id = request.form.get('thread_id')
        
        # If thread_id is provided, validate it belongs to this user
        if provided_thread_id:
            if not _validate_thread_id(provided_thread_id, user_id):
                return jsonify({'error': 'Invalid thread_id. You can only use your own threads.'}), 403
            thread_id = provided_thread_id
        else:
            # Generate thread_id based on user_id
            thread_id = _get_thread_id(user_id, conversation_id)
        
        filename = file.filename

        # Read file bytes
        file_bytes = file.read()
        if not file_bytes:
            return jsonify({'error': 'File is empty'}), 400

        # Ingest the PDF
        result = ingest_pdf(
            file_bytes=file_bytes,
            thread_id=thread_id,
            filename=filename
        )

        return jsonify({
            'success': True,
            'message': 'PDF ingested successfully',
            'thread_id': thread_id,
            'filename': result['filename'],
            'documents': result['documents'],
            'chunks': result['chunks']
        })

    except ValueError as e:
        logger.error(f"Value error in ingest: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error ingesting PDF: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to ingest PDF: {str(e)}'}), 500


@bp.route('/chat', methods=['POST'])
@login_required
def chat():
    """
    Chat with the RAG-enabled chatbot.
    Accepts JSON or form-data with 'message' and optionally 'thread_id' or 'conversation_id'.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        # Try to get JSON data first
        data = request.get_json(force=True, silent=True)
        
        # If no JSON data, try form data
        if not data:
            if request.form:
                data = {
                    'message': request.form.get('message', '').strip(),
                    'thread_id': request.form.get('thread_id'),
                    'conversation_id': request.form.get('conversation_id', type=int)
                }
            elif request.args:  # Also support query parameters
                data = {
                    'message': request.args.get('message', '').strip(),
                    'thread_id': request.args.get('thread_id'),
                    'conversation_id': request.args.get('conversation_id', type=int)
                }
        
        if not data:
            return jsonify({'error': 'No data provided. Please send JSON or form-data with "message" field.'}), 400

        message = data.get('message', '').strip()
        if not message:
            return jsonify({'error': 'Message is required'}), 400
        
        # Get thread_id from request or generate default
        provided_thread_id = data.get('thread_id')
        conversation_id = data.get('conversation_id')
        
        # If thread_id is provided, validate it belongs to this user
        if provided_thread_id:
            if not _validate_thread_id(provided_thread_id, user_id):
                return jsonify({'error': 'Invalid thread_id. You can only access your own threads.'}), 403
            thread_id = provided_thread_id
        else:
            # Generate thread_id based on user_id
            thread_id = _get_thread_id(user_id, conversation_id)

        # Prepare config for LangGraph
        config = {
            "configurable": {
                "thread_id": thread_id
            }
        }

        # Create HumanMessage
        human_message = HumanMessage(content=message)

        # Invoke the chatbot - LangGraph returns the final state
        state = chatbot.invoke(
            {"messages": [human_message]},
            config=config
        )

        # Extract the last message from the state
        messages = state.get("messages", [])
        if not messages:
            response_content = "I'm sorry, I couldn't generate a response."
        else:
            # Get the last message (should be the AI response)
            last_msg = messages[-1]
            if hasattr(last_msg, 'content'):
                response_content = last_msg.content
            elif isinstance(last_msg, dict):
                response_content = last_msg.get('content', str(last_msg))
            else:
                response_content = str(last_msg)

        return jsonify({
            'success': True,
            'message': response_content,
            'thread_id': thread_id,
            'has_document': thread_has_document(thread_id)
        })

    except Exception as e:
        logger.error(f"Error in RAG chat: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to process chat: {str(e)}'}), 500


@bp.route('/thread/status/<thread_id>', methods=['GET'])
@login_required
def get_thread_status(thread_id):
    """
    Get the status of a thread, including whether it has a document.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        # Validate thread_id belongs to this user
        if not _validate_thread_id(thread_id, user_id):
            return jsonify({'error': 'Access denied. You can only access your own threads.'}), 403

        has_doc = thread_has_document(thread_id)
        metadata = thread_document_metadata(thread_id) if has_doc else {}

        return jsonify({
            'thread_id': thread_id,
            'has_document': has_doc,
            'metadata': metadata
        })

    except Exception as e:
        logger.error(f"Error getting thread status: {str(e)}")
        return jsonify({'error': f'Failed to get thread status: {str(e)}'}), 500


@bp.route('/thread/document/<thread_id>', methods=['GET'])
@login_required
def get_thread_document(thread_id):
    """
    Get document metadata for a thread.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        # Validate thread_id belongs to this user
        if not _validate_thread_id(thread_id, user_id):
            return jsonify({'error': 'Access denied. You can only access your own threads.'}), 403

        if not thread_has_document(thread_id):
            return jsonify({
                'error': 'No document found for this thread',
                'thread_id': thread_id
            }), 404

        metadata = thread_document_metadata(thread_id)
        return jsonify({
            'thread_id': thread_id,
            'metadata': metadata
        })

    except Exception as e:
        logger.error(f"Error getting thread document: {str(e)}")
        return jsonify({'error': f'Failed to get thread document: {str(e)}'}), 500

