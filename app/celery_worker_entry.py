"""
Celery worker entry point. Creates the Flask app so init_celery runs and tasks
execute with Flask application context (required for get_db(), current_app, etc.).

Usage: celery -A app.celery_worker_entry.celery worker --loglevel=info
"""
from app import create_app
from app.celery_app import celery

# Create Flask app - this calls init_celery(app) when USE_CELERY_FOR_INGESTION is True,
# which sets celery.Task = ContextTask so all tasks run with app.app_context().
create_app()
