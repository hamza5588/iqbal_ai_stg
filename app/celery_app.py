"""
Celery application initialization for background task processing.
"""
from celery import Celery
from app.config import Config

# Create Celery instance
celery = Celery(
    'iqbalai',
    broker=Config.CELERY_BROKER_URL,
    backend=Config.CELERY_RESULT_BACKEND
)

# Update Celery configuration from Config
celery.conf.update(
    accept_content=Config.CELERY_ACCEPT_CONTENT,
    task_serializer=Config.CELERY_TASK_SERIALIZER,
    result_serializer=Config.CELERY_RESULT_SERIALIZER,
    timezone=Config.CELERY_TIMEZONE,
    task_track_started=Config.CELERY_TASK_TRACK_STARTED,
    task_time_limit=Config.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=Config.CELERY_TASK_SOFT_TIME_LIMIT,
)

def init_celery(app):
    """
    Initialize Celery with Flask app context.
    This should be called after Flask app is created.
    
    Args:
        app: Flask application instance
    """
    # Update config from Flask app config if available
    celery.conf.update(
        broker_url=app.config.get('CELERY_BROKER_URL', Config.CELERY_BROKER_URL),
        result_backend=app.config.get('CELERY_RESULT_BACKEND', Config.CELERY_RESULT_BACKEND),
    )
    
    class ContextTask(celery.Task):
        """Make celery tasks work with Flask app context."""
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)
    
    celery.Task = ContextTask
    
    return celery
