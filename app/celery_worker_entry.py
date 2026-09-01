"""
Celery worker entry point. Uses a minimal Flask app so workers avoid the full
web startup path (blueprints, LangGraph, Milvus health checks). Tasks still
execute inside a Flask app context for get_db() and current_app.
"""
import os

os.environ.setdefault("SKIP_DB_INIT", "true")
os.environ.setdefault("SKIP_EXTRA_STARTUP", "true")

from flask import Flask
from app.config import Config
from app.celery_app import celery, init_celery
from app.utils.db import close_db


def _create_worker_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
    app.teardown_appcontext(close_db)
    if app.config.get("USE_CELERY_FOR_INGESTION", False):
        init_celery(app)
    return app


_create_worker_app()
