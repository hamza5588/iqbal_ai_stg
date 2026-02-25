from flask import Blueprint, request, jsonify, session, current_app, send_file
from app.rbac.decorators import admin_only
from app.utils.db import get_db
from app.models.database_models import Lesson, User, ChatHistory, LessonChatHistory, RAGThread
from app.load_testing.models import TestUserSet, LoadTestResult, LoadTestStatus, LoadTestLog, TestMessageCSV
from app.load_testing.config import TestType, TargetEnvironment, LoadTestConfig
from app.load_testing.user_set_manager import UserSetManager
from app.load_testing.document_set_manager import DocumentSetManager
from app.load_testing.message_csv_manager import MessageCSVManager
from app.load_testing.runner import LoadTestRunner
from app.load_testing.report import ReportGenerator
import threading
import asyncio
import logging
import io
import os
import re
from pathlib import Path
from datetime import datetime
from app.utils.rag_service import MARKDOWN_EXPORTS_DIR

bp = Blueprint('load_test', __name__)
logger = logging.getLogger(__name__)

# Global dictionary to track active runners
active_runners = {}

def run_async_test(runner):
    """Helper to run async test in a thread"""
    asyncio.run(runner.run())

def run_analysis_bg(app, test_id, api_key):
    """Helper to run analysis in a background thread"""
    with app.app_context():
        try:
            generator = ReportGenerator(test_id)
            analysis = generator.generate_llm_analysis(api_key)
            generator.save_analysis(analysis)
        except Exception as e:
            logger.error(f"Error in background analysis thread: {str(e)}")

def sanitize_filename(name, ext=".md"):
    """Helper to create clean filenames with correct extensions"""
    # Remove existing common extensions
    for suffix in [".pdf", ".csv", ".txt", ".md"]:
        if name.lower().endswith(suffix):
            name = name[:-len(suffix)]
    
    # Replace spaces and special chars with underscores
    name = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')
    return f"{name}{ext}"

@bp.route('/start', methods=['POST'])
@admin_only
def start_test():
    """Start a new load test"""
    data = request.json
    try:
        # Parse config
        config_data = data.get('config', {})
        test_type_str = config_data.get('test_type')
        target_env_str = config_data.get('target_env')
        
        if not test_type_str or not target_env_str:
            return jsonify({'error': 'Missing test_type or target_env'}), 400
            
        # Create config object
        config = LoadTestConfig(
            test_type=TestType(test_type_str),
            target_env=TargetEnvironment(target_env_str),
            custom_url=config_data.get('custom_url'),
            concurrent_users=int(config_data.get('concurrent_users', 1)),
            duration_seconds=int(config_data.get('duration_seconds', 60)),
            ramp_up_seconds=int(config_data.get('ramp_up_seconds', 0)),
            test_user_set_id=int(config_data.get('test_user_set_id')) if config_data.get('test_user_set_id') else None,
            test_doc_set_id=int(config_data.get('test_doc_set_id')) if config_data.get('test_doc_set_id') else None,
            csv_file_id=int(config_data.get('csv_file_id')) if config_data.get('csv_file_id') else None,
            requests_per_user=int(config_data.get('requests_per_user', 10)),
            lesson_id=config_data.get('lesson_id'),
            headless=config_data.get('headless', True),
            stop_on_error=config_data.get('stop_on_error', False)
        )
        
        # Create Result record
        db = get_db()
        result = LoadTestResult(
            test_type=config.test_type.value,
            status=LoadTestStatus.PENDING.value,
            config=config_data,
            metrics={}
        )
        db.add(result)
        db.commit()
        
        use_celery = current_app.config.get('USE_CELERY_FOR_INGESTION', False)
        
        if use_celery:
            from app.tasks.load_test_tasks import run_load_test_task
            task = run_load_test_task.delay(config_data, result.id)
            return jsonify({
                'success': True,
                'message': 'Test started via Celery',
                'test_id': result.id,
                'task_id': task.id
            })
        else:
            runner = LoadTestRunner(current_app._get_current_object(), config, result.id)
            active_runners[result.id] = runner
            
            thread = threading.Thread(target=run_async_test, args=(runner,))
            thread.daemon = True
            thread.start()
            
            return jsonify({
                'success': True,
                'message': 'Test started via Thread',
                'test_id': result.id
            })
        
    except ValueError as e:
        return jsonify({'error': f"Invalid configuration: {str(e)}"}), 400
    except Exception as e:
        logger.error(f"Failed to start test: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/stop/<int:test_id>', methods=['POST'])
@admin_only
def stop_test(test_id):
    """Stop a running test (Global DB-backed stop)"""
    db = get_db()
    result = db.query(LoadTestResult).get(test_id)
    if not result:
        return jsonify({'error': 'Test not found'}), 404
        
    result.status = LoadTestStatus.STOPPED.value
    db.commit()
    
    if test_id in active_runners:
        active_runners[test_id].stop()
        
    return jsonify({'message': 'Stop signal sent to database'})

@bp.route('/status/<int:test_id>', methods=['GET'])
@admin_only
def get_status(test_id):
    """Get test status"""
    db = get_db()
    result = db.query(LoadTestResult).get(test_id)
    if not result:
        return jsonify({'error': 'Test not found'}), 404
    
    return jsonify({
        'status': result.status,
        'started_at': result.started_at.isoformat() if result.started_at else None,
        'completed_at': result.completed_at.isoformat() if result.completed_at else None,
        'metrics': result.metrics
    })

@bp.route('/status/<int:test_id>/logs', methods=['GET'])
@admin_only
def get_test_logs(test_id):
    """Get recent logs for a test (live streaming)"""
    db = get_db()
    since = request.args.get('since')
    
    query = db.query(LoadTestLog).filter_by(result_id=test_id)
    if since:
        query = query.filter(LoadTestLog.timestamp > datetime.fromisoformat(since))
    
    logs = query.order_by(LoadTestLog.timestamp.asc(), LoadTestLog.id.asc()).limit(100).all()
    
    return jsonify([{
        'level': l.level,
        'message': l.message,
        'timestamp': l.timestamp.isoformat()
    } for l in logs])

@bp.route('/results', methods=['GET'])
@admin_only
def list_results():
    """List recent test results"""
    db = get_db()
    results = db.query(LoadTestResult).order_by(LoadTestResult.started_at.desc()).limit(20).all()
    return jsonify([{
        'id': r.id,
        'test_type': r.test_type,
        'status': r.status,
        'started_at': r.started_at.isoformat() if r.started_at else None,
        'metrics': r.metrics
    } for r in results])

@bp.route('/results/<int:test_id>', methods=['DELETE'])
@admin_only
def delete_result(test_id):
    """Delete a specific test result and its associated artifacts/history"""
    db = get_db()
    result = db.query(LoadTestResult).get(test_id)
    if not result:
        return jsonify({'error': 'Result not found'}), 404
    
    try:
        # --- Selective Purging (Files & RAG Threads) ---
        metrics = result.metrics or {}
        artifacts = metrics.get('artifacts', [])
        
        # Extract unique thread IDs from this specific test
        thread_ids = set()
        for art in artifacts:
            t_id = art.get('thread_id')
            if t_id:
                thread_ids.add(t_id)
        
        for t_id in thread_ids:
            # 1. Purge Markdown Artifacts from Disk
            # Only delete if it matches the thread_id
            matches = list(MARKDOWN_EXPORTS_DIR.glob(f"{t_id}_*.md"))
            for match in matches:
                try:
                    os.remove(match)
                    logger.info(f"Purged residue artifact: {match.name}")
                except Exception as e:
                    logger.error(f"Failed to purge artifact {match.name}: {e}")
            
            # 2. Purge RAG History from DB
            # This will cascade to RAGChunks automatically
            db.query(RAGThread).filter_by(thread_id=t_id).delete()
            logger.info(f"Purged residue RAG Thread: {t_id}")

        # 3. Purge Logs and Result Record
        db.query(LoadTestLog).filter_by(result_id=test_id).delete()
        db.delete(result)
        db.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error during result deletion: {e}")
        return jsonify({'error': f"Partial deletion occurred: {str(e)}"}), 500

@bp.route('/results/all', methods=['DELETE'])
@admin_only
def delete_all_results():
    """Delete all test results and associated artifacts/history selectively"""
    db = get_db()
    try:
        results = db.query(LoadTestResult).all()
        
        # Collect all thread_ids from all results
        all_thread_ids = set()
        for result in results:
            metrics = result.metrics or {}
            artifacts = metrics.get('artifacts', [])
            for art in artifacts:
                t_id = art.get('thread_id')
                if t_id:
                    all_thread_ids.add(t_id)
        
        # Purge all collected artifacts and threads
        for t_id in all_thread_ids:
            # 1. Purge Files
            matches = list(MARKDOWN_EXPORTS_DIR.glob(f"{t_id}_*.md"))
            for match in matches:
                try:
                    os.remove(match)
                except:
                    pass
            
            # 2. Purge RAG History
            db.query(RAGThread).filter_by(thread_id=t_id).delete()

        # 3. Final database wipe
        db.query(LoadTestLog).delete()
        db.query(LoadTestResult).delete()
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        logger.error(f"Error during bulk deletion: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/lessons', methods=['GET'])
@admin_only
def list_lessons():
    """List all finalized lessons for the dropdown"""
    db = get_db()
    lessons = db.query(Lesson).filter(Lesson.status == 'finalized').order_by(Lesson.created_at.desc()).all()
    return jsonify([{
        'id': l.id,
        'title': l.title
    } for l in lessons])

@bp.route('/users/create', methods=['POST'])
@admin_only
def create_user_set():
    """Create a new set of test users"""
    data = request.json
    name = data.get('name')
    role = data.get('role', 'student')
    count = int(data.get('count', 10))
    password = data.get('password', 'TestPass123!')
    set_prompt = (data.get('set_prompt') or '').strip() or None
    
    user_set, errors = UserSetManager.create_user_set(name, role, count, password, set_prompt)
    
    if user_set:
        return jsonify({
            'success': True,
            'user_set_id': user_set.id,
            'count': user_set.user_count,
            'errors': errors
        })
    else:
        return jsonify({'error': 'Failed to create user set', 'details': errors}), 500

@bp.route('/users', methods=['GET'])
@admin_only
def list_user_sets():
    """List all user sets"""
    sets = UserSetManager.get_all_sets()
    return jsonify([{
        'id': s.id,
        'name': s.name,
        'role': s.role,
        'user_count': s.user_count,
        'created_at': s.created_at.isoformat(),
        'set_prompt': s.set_prompt
    } for s in sets])

@bp.route('/users/<int:set_id>', methods=['DELETE'])
@admin_only
def delete_user_set(set_id):
    """Delete a user set"""
    success = UserSetManager.delete_user_set(set_id)
    if success:
        return jsonify({'success': True})
    return jsonify({'error': 'Failed to delete user set'}), 500

@bp.route('/doc-sets', methods=['GET'])
@admin_only
def list_doc_sets():
    """List all document sets"""
    sets = DocumentSetManager.get_all_sets()
    return jsonify([{
        'id': s.id,
        'name': s.name,
        'created_at': s.created_at.isoformat(),
        'doc_count': len(s.documents) if s.documents else 0,
        'documents': [{
            'id': d.id,
            'filename': d.filename,
            'file_size': d.file_size_bytes
        } for d in (s.documents or [])]
    } for s in sets])

@bp.route('/doc-sets/create', methods=['POST'])
@admin_only
def create_doc_set():
    """Create a new document set"""
    try:
        data = request.json
        name = data.get('name')
        if not name:
            return jsonify({'error': 'Name is required'}), 400
            
        doc_set = DocumentSetManager.create_document_set(name)
        return jsonify({
            'success': True,
            'id': doc_set.id,
            'name': doc_set.name
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/doc-sets/<int:set_id>/upload', methods=['POST'])
@admin_only
def upload_document(set_id):
    """Upload a document to a set"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are allowed'}), 400
        
    doc, error = DocumentSetManager.add_document(set_id, file)
    
    if doc:
        return jsonify({
            'success': True,
            'doc_id': doc.id,
            'filename': doc.filename
        })
    else:
        return jsonify({'error': error}), 500

@bp.route('/doc-sets/<int:set_id>', methods=['DELETE'])
@admin_only
def delete_doc_set(set_id):
    """Delete a document set"""
    success = DocumentSetManager.delete_document_set(set_id)
    if success:
        return jsonify({'success': True})
    return jsonify({'error': 'Failed to delete document set'}), 500

@bp.route('/report/<int:test_id>/technical', methods=['GET'])
@admin_only
def get_technical_report(test_id):
    """Get technical report"""
    generator = ReportGenerator(test_id)
    report = generator.generate_technical_report()
    return jsonify(report)

@bp.route('/report/<int:test_id>/executive', methods=['POST'])
@admin_only
def generate_executive_report(test_id):
    """Generate executive report using LLM"""
    # Correct 3-tier API key lookup matching llm_factory.py pattern:
    # 1. Per-user session key (set at login or via /api_key/update)
    # 2. Admin SystemSettings 'groq_api_key' (encrypted, set in Admin Dashboard LLM Settings)
    # 3. Environment variable / app config fallback
    api_key = session.get('groq_api_key') or ''

    if not api_key:
        # Check Admin SystemSettings (the key configured in Admin Dashboard → LLM Settings)
        try:
            from app.models.database_models import SystemSettings
            from app.utils.encryption import decrypt_api_key
            db = get_db()
            api_key_setting = db.query(SystemSettings).filter(
                SystemSettings.key == 'groq_api_key'
            ).first()
            if api_key_setting and api_key_setting.value:
                api_key = decrypt_api_key(api_key_setting.value)
        except Exception as e:
            logger.warning(f"Could not retrieve admin Groq API key from SystemSettings: {e}")

    if not api_key:
        # Final fallback: GROQ_API_KEY env var
        api_key = current_app.config.get('GROQ_API_KEY', '')


    
    generator = ReportGenerator(test_id)
    report = generator.generate_technical_report()
    
    # Check if analysis already exists
    if report['summary'].get('llm_analysis'):
        report['analysis'] = report['summary'].get('llm_analysis') # For frontend compatibility
        return jsonify(report)
    
    # Start generation in background
    if current_app.config.get('USE_CELERY_FOR_INGESTION', False):
        from app.tasks.load_test_tasks import generate_analysis_task
        generate_analysis_task.delay(test_id, api_key)
    else:
        thread = threading.Thread(
            target=run_analysis_bg, 
            args=(current_app._get_current_object(), test_id, api_key)
        )
        thread.start()
    
    return jsonify({
        'status': 'generating',
        'message': 'AI analysis is being generated in the background'
    })

@bp.route('/message-csvs', methods=['GET'])
@admin_only
def list_message_csvs():
    """List all message CSVs"""
    csvs = MessageCSVManager.get_all_csvs()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'message_count': c.message_count,
        'created_at': c.created_at.isoformat()
    } for c in csvs])

@bp.route('/message-csvs/upload', methods=['POST'])
@admin_only
def upload_message_csv():
    """Upload a new message CSV"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
        
    file = request.files['file']
    name = request.form.get('name', file.filename)
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if not file.filename.lower().endswith('.csv'):
        return jsonify({'error': 'Only CSV files are allowed'}), 400
        
    csv_record, error = MessageCSVManager.create_message_csv(name, file)
    
    if csv_record:
        return jsonify({
            'success': True,
            'id': csv_record.id,
            'name': csv_record.name,
            'message_count': csv_record.message_count
        })
    else:
        return jsonify({'error': error}), 500

@bp.route('/message-csvs/<int:csv_id>', methods=['DELETE'])
@admin_only
def delete_message_csv(csv_id):
    """Delete a message CSV"""
    success = MessageCSVManager.delete_message_csv(csv_id)
    if success:
        return jsonify({'success': True})
    return jsonify({'error': 'Failed to delete message CSV'}), 500

@bp.route('/report/<int:test_id>/artifacts', methods=['GET'])
@admin_only
def get_test_artifacts(test_id):
    """Get artifact list for a test"""
    db = get_db()
    result = db.query(LoadTestResult).get(test_id)
    if not result:
        return jsonify({'error': 'Test not found'}), 404
    
    metrics = result.metrics or {}
    artifacts = metrics.get('artifacts', [])
    
    return jsonify({'artifacts': artifacts})

@bp.route('/artifact/download', methods=['GET'])
@admin_only
def download_artifact():
    """Download a specific artifact as Markdown"""
    test_id = request.args.get('test_id')
    user_email = request.args.get('user_email')
    artifact_type = request.args.get('type')
    doc_name = request.args.get('doc_name', 'document')
    
    # Optional IDs
    thread_id = request.args.get('thread_id')
    conversation_id = request.args.get('conversation_id')
    lesson_id = request.args.get('lesson_id')
    
    if not test_id or not artifact_type:
        return jsonify({'error': 'test_id and type are required'}), 400

    db = get_db()
    
    # Better filename construction
    base_name = f"load_test_{test_id}_{user_email}_{artifact_type}_{doc_name}"
    filename = sanitize_filename(base_name, ".md")

    # Color coding helper
    def get_latency_emoji(latency):
        if latency < 3.0: return "🟢"
        if latency <= 7.0: return "🟡"
        return "🔴"

    if artifact_type == 'extracted_text' and thread_id:
        # Search for file in MARKDOWN_EXPORTS_DIR
        matches = list(MARKDOWN_EXPORTS_DIR.glob(f"{thread_id}_*.md"))
        if matches:
            # We use the sanitized name for the download
            return send_file(str(matches[0]), as_attachment=True, download_name=filename)
        else:
            return jsonify({'error': 'Extracted text file not found'}), 404

    elif artifact_type == 'chat_transcript':
        # Reconstruct from captured metadata in result.metrics
        result = db.query(LoadTestResult).get(int(test_id))
        if not result:
            return jsonify({'error': 'Test not found'}), 404
        
        artifacts = (result.metrics or {}).get('artifacts', [])
        target = None
        
        # Match the specific artifact by user and conversation/lesson/doc_name
        for art in artifacts:
            if art.get('user_email') == user_email and art.get('type') == 'chat_transcript':
                # Check IDs loosely (convert to str for safety)
                art_conv_id = str(art.get('conversation_id')) if art.get('conversation_id') else ''
                art_lesson_id = str(art.get('lesson_id')) if art.get('lesson_id') else ''
                
                if (conversation_id and str(conversation_id) == art_conv_id) or \
                   (lesson_id and str(lesson_id) == art_lesson_id) or \
                   (doc_name and art.get('doc_name') == doc_name):
                    target = art
                    break
        
        if not target:
            return jsonify({'error': 'Transcript data not found in report'}), 404
        
        transcript = target.get('transcript', [])
        content = f"# Chat Transcript: {doc_name}\n"
        content += f"**User:** {user_email}\n"
        content += f"**Test:** {result.test_type} (ID: {test_id})\n"
        if target.get('keyword_hits') is not None:
            content += f"**Keyword Hits:** {target.get('keyword_hits')}\n"
        content += "\n---\n\n"
        
        for msg in transcript:
            role = "User" if msg['role'] == 'user' else "Assistant"
            content += f"### {role}\n{msg['content']}\n\n"
            if 'latency' in msg:
                emoji = get_latency_emoji(msg['latency'])
                content += f"> [!NOTE]\n> **Response Time:** {emoji} {msg['latency']:.2f}s\n\n"

    elif artifact_type == 'lesson_content' and lesson_id:
        lesson = db.query(Lesson).get(int(lesson_id))
        if lesson:
            content = f"# Lesson: {lesson.title}\n"
            content += f"**User:** {user_email}\n"
            content += f"**Test ID:** {test_id}\n\n---\n\n"
            content += lesson.content
        else:
            return jsonify({'error': f'Lesson ID {lesson_id} not found in database'}), 404
    else:
        # If lesson_id was expected but missing
        if artifact_type == 'lesson_content' and not lesson_id:
            return jsonify({'error': 'lesson_id is required for lesson_content'}), 400

    if content:
        buffer = io.BytesIO()
        buffer.write(content.encode('utf-8'))
        buffer.seek(0)
        return send_file(
            buffer, 
            as_attachment=True, 
            download_name=filename, 
            mimetype='text/markdown'
        )

    return jsonify({'error': 'Invalid artifact request or missing data'}), 400
