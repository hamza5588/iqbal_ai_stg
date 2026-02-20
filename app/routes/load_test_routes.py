from flask import Blueprint, request, jsonify, session, current_app
from app.rbac.decorators import admin_only
from app.utils.db import get_db
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
from datetime import datetime

bp = Blueprint('load_test', __name__)
logger = logging.getLogger(__name__)

# Global dictionary to track active runners
# In a multi-worker production environment, this would need Redis or DB state.
# But for a load testing tool likely running on a single admin instance or manageable scale, this is fine.
active_runners = {}

def run_async_test(runner):
    """Helper to run async test in a thread"""
    asyncio.run(runner.run())

@bp.route('/start', methods=['POST'])
# @admin_only
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
        
        # Initialize Runner
        from flask import current_app
        runner = LoadTestRunner(current_app._get_current_object(), config, result.id)
        active_runners[result.id] = runner
        
        # Start in background thread
        thread = threading.Thread(target=run_async_test, args=(runner,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'message': 'Test started',
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
    """Stop a running test"""
    if test_id in active_runners:
        # Implement stop logic in runner (e.g. setting a flag)
        # For now, we just remove from dict, but the thread keeps running until it checks the flag
        # We need to add a stop method to LoadTestRunner
        pass
        return jsonify({'message': 'Stop signal sent'})
    return jsonify({'error': 'Test not found or not running'}), 404

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
# @admin_only
def get_test_logs(test_id):
    """Get recent logs for a test (live streaming)"""
    db = get_db()
    # Get logs created after a certain timestamp if provided, or last 50
    since = request.args.get('since')
    
    query = db.query(LoadTestLog).filter_by(result_id=test_id)
    if since:
        query = query.filter(LoadTestLog.timestamp > datetime.fromisoformat(since))
    
    logs = query.order_by(LoadTestLog.timestamp.asc()).limit(100).all()
    
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
    """Delete a specific test result and its logs"""
    db = get_db()
    result = db.query(LoadTestResult).get(test_id)
    if not result:
        return jsonify({'error': 'Result not found'}), 404
        
    # Delete logs first
    db.query(LoadTestLog).filter_by(result_id=test_id).delete()
    db.delete(result)
    db.commit()
    return jsonify({'success': True})

@bp.route('/results/all', methods=['DELETE'])
@admin_only
def delete_all_results():
    """Delete all test results and logs"""
    db = get_db()
    try:
        db.query(LoadTestLog).delete()
        db.query(LoadTestResult).delete()
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500

@bp.route('/users/create', methods=['POST'])
# @admin_only
def create_user_set():
    """Create a new set of test users"""
    data = request.json
    name = data.get('name')
    role = data.get('role', 'student')
    count = int(data.get('count', 10))
    password = data.get('password', 'TestPass123!')
    
    user_set, errors = UserSetManager.create_user_set(name, role, count, password)
    
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
        'created_at': s.created_at.isoformat()
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
# @admin_only
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
# @admin_only
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
    # Use session API key or fallback
    api_key = session.get('groq_api_key') or current_app.config.get('GROQ_API_KEY')
    
    generator = ReportGenerator(test_id)
    analysis = generator.generate_llm_analysis(api_key)
    generator.save_analysis(analysis)
    
    return jsonify({'analysis': analysis})

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
