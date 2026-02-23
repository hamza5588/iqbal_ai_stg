"""
Celery tasks for load testing.
"""
import logging
from app.celery_app import celery
from app.load_testing.runner import LoadTestRunner
from app.load_testing.config import LoadTestConfig, TestType, TargetEnvironment

logger = logging.getLogger(__name__)

@celery.task(bind=True, name='app.tasks.load_test_tasks.run_load_test_task')
def run_load_test_task(self, config_data: dict, result_id: int):
    """
    Celery task to run a load test scenario.
    """
    from app import create_app
    app = create_app()
    with app.app_context():
        try:
            # Reconstruct config object
            config = LoadTestConfig(
                test_type=TestType(config_data.get('test_type')),
                target_env=TargetEnvironment(config_data.get('target_env')),
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
            
            # Initialize Runner
            runner = LoadTestRunner(app, config, result_id)
            
            # Run the test
            # Since this is a Celery task, it will block until done, which is intended.
            import asyncio
            asyncio.run(runner.run())
            
            return {'success': True, 'result_id': result_id}
        except Exception as e:
            logger.error(f"Error in run_load_test_task: {str(e)}", exc_info=True)
            return {'success': False, 'error': str(e)}

@celery.task(bind=True, name='app.tasks.load_test_tasks.generate_analysis_task')
def generate_analysis_task(self, test_id: int, api_key: str):
    """
    Celery task to generate AI executive analysis for a report.
    """
    from app import create_app
    from app.load_testing.report import ReportGenerator
    
    app = create_app()
    with app.app_context():
        try:
            generator = ReportGenerator(test_id)
            analysis = generator.generate_llm_analysis(api_key)
            generator.save_analysis(analysis)
            return {'success': True, 'test_id': test_id}
        except Exception as e:
            logger.error(f"Error in generate_analysis_task: {str(e)}", exc_info=True)
            return {'success': False, 'error': str(e)}
