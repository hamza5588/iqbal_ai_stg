# # app/__init__.py
# from flask import Flask
# from datetime import timedelta
# import os
# from app.utils.db import init_db
# from flask_mail import Mail
# from app.config import Config

# mail = Mail()

# def create_app():
#     # Create Flask app with correct template folder
#     base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#     template_dir = os.path.join(base_dir, 'templates')
#     static_dir = os.path.join(base_dir, 'app', 'static')
    
#     app = Flask(__name__, 
#                 template_folder=template_dir,
#                 static_folder=static_dir)
    
#     # Load configuration
#     app.config.from_object(Config)
    
#     # Configure session
#     app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
#     app.config['SESSION_COOKIE_SECURE'] = True
#     app.config['SESSION_COOKIE_HTTPONLY'] = True 
#     app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    
#     # Initialize Flask-Mail
#     mail.init_app(app)
    
#     # Ensure template folder exists
#     if not os.path.exists(app.template_folder):
#         os.makedirs(app.template_folder)
#         print(f"Created template folder at: {app.template_folder}")
#     else:
#         print(f"Template folder exists at: {app.template_folder}")
    
#     # Initialize database
#     with app.app_context():
#         init_db(app)
    
#     # Register blueprints (import here to avoid circular imports)
#     from app.routes.auth import bp as auth_bp
#     from app.routes.chat import bp as chat_bp
#     from app.routes.api_key import bp as api_key_bp
#     from app.routes.files import bp as file_bp
#     from app.routes.chatbot_routes import bp as chatbot_bp  # Changed to match pattern
#     from app.routes.survey import bp as survey_bp  # Separate import

#     # Register blueprints with appropriate prefixes
#     app.register_blueprint(auth_bp, url_prefix='/auth')  # Only register once
#     app.register_blueprint(chat_bp)
#     app.register_blueprint(api_key_bp)
#     app.register_blueprint(file_bp)
#     app.register_blueprint(chatbot_bp, url_prefix='/api')
#     app.register_blueprint(survey_bp, url_prefix='/api')
    
#     print(f"Flask app template folder: {app.template_folder}")
    
#     return app














# # app/__init__.py
# from flask import Flask
# from flask_cors import CORS  
# from datetime import timedelta
# import os
# from app.utils.db import init_db
# from flask_mail import Mail
# from app.config import Config

# mail = Mail()

# def create_app():
#     # Create Flask app with correct template folder
#     base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#     template_dir = os.path.join(base_dir, 'templates')
#     static_dir = os.path.join(base_dir, 'app', 'static')
    
#     app = Flask(__name__, 
#                 template_folder=template_dir,
#                 static_folder=static_dir)
    
#     # Configure CORS - Add this right after Flask app creation
#     CORS(app,
#          resources={
#              r"/api/*": {
#                  "origins": "*",
#                  "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
#                  "allow_headers": ["Content-Type", "Authorization"]
#              },
#              r"/auth/*": {
#                  "origins": "*",
#                  "supports_credentials": True
#              }
#          },
#          supports_credentials=True)
    
#     # Load configuration
#     app.config.from_object(Config)
    
#     # Configure session
#     app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
#     app.config['SESSION_COOKIE_SECURE'] = True
#     app.config['SESSION_COOKIE_HTTPONLY'] = True 
#     app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    
#     # Initialize Flask-Mail
#     mail.init_app(app)
    
#     # Ensure template folder exists
#     if not os.path.exists(app.template_folder):
#         os.makedirs(app.template_folder)
#         print(f"Created template folder at: {app.template_folder}")
#     else:
#         print(f"Template folder exists at: {app.template_folder}")
    
#     # Initialize database
#     with app.app_context():
#         init_db(app)
    
#     # Register blueprints (import here to avoid circular imports)
#     from app.routes.auth import bp as auth_bp
#     from app.routes.chat import bp as chat_bp
#     from app.routes.api_key import bp as api_key_bp
#     from app.routes.files import bp as file_bp
#     from app.routes.chatbot_routes import bp as chatbot_bp
#     from app.routes.survey import bp as survey_bp

#     # Register blueprints with appropriate prefixes
#     app.register_blueprint(auth_bp, url_prefix='/auth')
#     app.register_blueprint(chat_bp)
#     app.register_blueprint(api_key_bp)
#     app.register_blueprint(file_bp)
#     app.register_blueprint(chatbot_bp, url_prefix='/api')
#     app.register_blueprint(survey_bp, url_prefix='/api')
    
#     print(f"Flask app template folder: {app.template_folder}")
    
#     return app









# app/__init__.py
from flask import Flask, request, redirect
from flask_cors import CORS  
from datetime import timedelta
import os

# Disable tqdm threading and tokenizer parallelism to prevent "cannot start new thread" errors
# This must be set BEFORE any imports that use these libraries
os.environ['TQDM_DISABLE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

from app.utils.db import init_db, close_db
from flask_mail import Mail
from app.config import Config
from app.celery_app import init_celery



mail = Mail()

def create_app():
    # Create Flask app with correct template folder
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_dir = os.path.join(base_dir, 'templates')
    static_dir = os.path.join(base_dir, 'static')
    
    app = Flask(__name__, 
                template_folder=template_dir,
                static_folder=static_dir)
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


    
    # Configure CORS - FIXED VERSION
    CORS(app,
         resources={
             r"/api/*": {
                 "origins": ["http://localhost:3000", "http://localhost:8080", "http://127.0.0.1:3000", "http://127.0.0.1:8080"],  # Specify your frontend URLs
                 "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                 "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
                 "supports_credentials": True,  # This was missing!
                 "expose_headers": ["Content-Type", "Authorization"]
             },
             r"/auth/*": {
                 "origins": ["http://localhost:3000", "http://localhost:8080", "http://127.0.0.1:3000", "http://127.0.0.1:8080"],
                 "supports_credentials": True,
                 "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                 "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"]
             }
         },
         supports_credentials=True)
    
    # Load configuration
    app.config.from_object(Config)
    
    # Set max content length for file uploads (100MB)
    app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
    
    # Configure session - UPDATED for CORS
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
    # Use secure cookies in non-local environments so sessions are never sent
    # over plain HTTP in staging/production. Local dev continues to work on http.
    is_local_env = str(app.config.get('ENV', 'local')).lower() == 'local'
    app.config['SESSION_COOKIE_SECURE'] = not is_local_env
    app.config['SESSION_COOKIE_HTTPONLY'] = True 
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    
    # Initialize Flask-Mail
    mail.init_app(app)
    
    # Ensure template folder exists
    if not os.path.exists(app.template_folder):
        os.makedirs(app.template_folder)
        print(f"Created template folder at: {app.template_folder}")
    else:
        print(f"Template folder exists at: {app.template_folder}")
    
    # Register database cleanup function
    app.teardown_appcontext(close_db)
    
    # Ensure phase1_models are imported so SQLAlchemy registers them BEFORE init_db
    # (init_db calls Base.metadata.create_all which needs all models already registered)
    from app.models import phase1_models as _phase1_models  # noqa: F401

    # Initialize database and (optionally) heavy startup components.
    # For local verification scripts we can skip expensive startup work.
    skip_extra_startup = str(os.getenv("SKIP_EXTRA_STARTUP", "false")).lower() in ("1", "true", "yes")
    skip_db_init = str(os.getenv("SKIP_DB_INIT", "false")).lower() in ("1", "true", "yes")
    with app.app_context():
        if not skip_db_init:
            init_db(app)
        if not skip_extra_startup:
            # Create default admin account if it doesn't exist
            from app.utils.admin_init import create_default_admin
            create_default_admin()

            # Initialize Lesson Q&A LangGraph (Postgres checkpointer setup + compile).
            # This MUST run once on startup so that:
            #   - PostgresSaver.setup() creates checkpoint tables, and
            #   - the compiled graph is ready for use in request handlers.
            try:
                from app.services.lesson.lesson_qa_graph import init_lesson_qa_graph
                init_lesson_qa_graph()
            except Exception as e:
                # Fail loudly in logs; app startup should surface this in staging.
                import traceback
                print("Failed to initialize Lesson Q&A LangGraph:", e)
                traceback.print_exc()
    
    # Register blueprints (import here to avoid circular imports).
    # Heavy blueprints that depend on ML packages (LangGraph, Whisper, etc.) are
    # wrapped in try/except so tests and CI environments without the full ML stack
    # can still start the app and run service-layer tests.
    from app.routes.auth import bp as auth_bp
    from app.routes.admin_routes import bp as admin_bp
    from app.routes.school_routes import school_api_bp, school_ui_bp
    from app.routes.subscription import bp as subscription_bp

    try:
        from app.routes.chat import bp as chat_bp
        _chat_bp = chat_bp
    except ImportError as _e:
        import warnings; warnings.warn(f"chat blueprint unavailable: {_e}"); _chat_bp = None

    try:
        from app.routes.api_key import bp as api_key_bp
        _api_key_bp = api_key_bp
    except ImportError as _e:
        import warnings; warnings.warn(f"api_key blueprint unavailable: {_e}"); _api_key_bp = None

    try:
        from app.routes.files import bp as file_bp
        _file_bp = file_bp
    except ImportError as _e:
        import warnings; warnings.warn(f"files blueprint unavailable: {_e}"); _file_bp = None

    try:
        from app.routes.chatbot_routes import bp as chatbot_bp
        _chatbot_bp = chatbot_bp
    except ImportError as _e:
        import warnings; warnings.warn(f"chatbot blueprint unavailable: {_e}"); _chatbot_bp = None

    try:
        from app.routes.survey import bp as survey_bp
        _survey_bp = survey_bp
    except ImportError as _e:
        import warnings; warnings.warn(f"survey blueprint unavailable: {_e}"); _survey_bp = None

    try:
        from app.routes.lesson_routes import bp as lesson_bp
        _lesson_bp = lesson_bp
    except ImportError as _e:
        import warnings; warnings.warn(f"lesson blueprint unavailable: {_e}"); _lesson_bp = None

    try:
        from app.routes.rag_routes import bp as rag_bp
        _rag_bp = rag_bp
    except ImportError as _e:
        import warnings; warnings.warn(f"rag blueprint unavailable: {_e}"); _rag_bp = None

    try:
        from app.routes.load_test_routes import bp as load_test_bp
        _load_test_bp = load_test_bp
    except ImportError as _e:
        import warnings; warnings.warn(f"load_test blueprint unavailable: {_e}"); _load_test_bp = None

    try:
        from app.routes.canonical_api import (
            api_coordinator_bp,
            api_lesson_ext_bp,
            api_principal_bp,
            api_quizzes_bp,
            api_schools_bp,
            api_student_bp,
            api_teacher_bp,
            auth_api_bp,
        )
        _canonical_bps = [
            api_coordinator_bp, api_lesson_ext_bp, api_principal_bp,
            api_quizzes_bp, api_schools_bp, api_student_bp,
            api_teacher_bp, auth_api_bp,
        ]
    except ImportError as _e:
        import warnings; warnings.warn(f"canonical_api blueprints unavailable: {_e}"); _canonical_bps = []

    # Register blueprints with appropriate prefixes
    app.register_blueprint(auth_bp, url_prefix='/auth')
    if _chat_bp:        app.register_blueprint(_chat_bp)
    if _api_key_bp:     app.register_blueprint(_api_key_bp, url_prefix='/api-key')
    if _file_bp:        app.register_blueprint(_file_bp)
    if _chatbot_bp:     app.register_blueprint(_chatbot_bp, url_prefix='/api')
    if _survey_bp:      app.register_blueprint(_survey_bp, url_prefix='/api')
    if _lesson_bp:      app.register_blueprint(_lesson_bp, url_prefix='/api/lessons')
    if _rag_bp:         app.register_blueprint(_rag_bp, url_prefix='/api/rag')
    app.register_blueprint(subscription_bp, url_prefix='/subscription')
    app.register_blueprint(admin_bp)
    if _load_test_bp:   app.register_blueprint(_load_test_bp, url_prefix='/api/load-test')
    app.register_blueprint(school_ui_bp)
    app.register_blueprint(school_api_bp)
    for _bp in _canonical_bps:
        app.register_blueprint(_bp)

    # Phase 1 blueprints
    from app.routes.notification_routes import notification_bp
    from app.routes.terms_routes import terms_bp
    from app.routes.user_admin_routes import user_admin_bp
    from app.routes.platform_content_routes import platform_admin_bp, content_library_bp
    from app.routes.syllabus_routes import syllabus_bp
    from app.routes.parent_routes import parent_bp
    from app.routes.archival_routes import archival_bp
    from app.routes.export_routes import export_bp
    from app.routes.i18n_routes import i18n_bp

    app.register_blueprint(notification_bp)
    app.register_blueprint(terms_bp)
    app.register_blueprint(user_admin_bp)
    app.register_blueprint(platform_admin_bp)
    app.register_blueprint(content_library_bp)
    app.register_blueprint(syllabus_bp)
    app.register_blueprint(parent_bp)
    app.register_blueprint(archival_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(i18n_bp)

    # Phase 2 blueprints
    from app.routes.phase2_routes import phase2_bp
    app.register_blueprint(phase2_bp)

    from app.routes.phase3_routes import phase3_api_bp
    app.register_blueprint(phase3_api_bp)

    from app.routes.phase4_routes import phase4_api_bp
    app.register_blueprint(phase4_api_bp)

    try:
        from app.socketio_phase4 import init_socketio

        sio = init_socketio(app)
        if sio is not None:
            app.extensions["socketio"] = sio
    except Exception as _sio_e:
        import warnings

        warnings.warn(f"Socket.IO init skipped: {_sio_e}")

    from app.routes.mini_lecture_api import mini_lecture_api_bp
    app.register_blueprint(mini_lecture_api_bp)

    from app.routes.calendar_oauth_routes import calendar_oauth_bp
    app.register_blueprint(calendar_oauth_bp)

    from app.routes.calendar_api_routes import calendar_api_bp
    app.register_blueprint(calendar_api_bp)

    # Register seed CLI commands
    from app.seeds import register_seeds
    register_seeds(app)

    # Serve teacher dashboard static assets (css, js, assets from teacherfrontend)
    from flask import send_from_directory
    teacherfrontend_dir = os.path.join(base_dir, 'teacherfrontend')
    @app.route('/teacher-static/<path:filename>')
    def teacher_static(filename):
        return send_from_directory(teacherfrontend_dir, filename)

    # Register RBAC template helpers
    from app.rbac.template_helpers import TEMPLATE_HELPERS
    for name, func in TEMPLATE_HELPERS.items():
        app.jinja_env.globals[name] = func

    # ------------------------------------------------------------------
    # Canonical HTTPS enforcement
    # ------------------------------------------------------------------
    @app.before_request
    def _llm_telemetry_request_context():
        try:
            from app.utils.llm_gateway import init_flask_request_llm_telemetry

            init_flask_request_llm_telemetry()
        except Exception:
            pass

    @app.teardown_request
    def _llm_telemetry_request_teardown(exc):
        try:
            from app.utils.llm_gateway import teardown_flask_request_llm_telemetry

            teardown_flask_request_llm_telemetry(exc)
        except Exception:
            pass

    @app.before_request
    def _enforce_terms_acceptance():
        """
        Block API requests from users who haven't accepted the current Terms of Service.
        Exempt: terms routes, auth routes, static files, pre-flight OPTIONS requests.
        Also skipped in test/CI environments (SKIP_EXTRA_STARTUP=true).
        """
        # In test/CI mode the middleware is disabled so existing tests don't need
        # to perform terms acceptance before every request.
        if skip_extra_startup:
            return None
        from flask import session, jsonify
        if request.method == "OPTIONS":
            return None
        path = request.path
        # Exempted path prefixes
        exempt_prefixes = ("/api/terms", "/auth/", "/static/", "/teacher-static/")
        if any(path.startswith(p) for p in exempt_prefixes):
            return None
        # Only enforce for authenticated API routes
        if not path.startswith("/api/"):
            return None
        user_id = session.get("user_id")
        if not user_id:
            return None  # Not logged in — other guards handle that
        try:
            from app.utils.db import get_db
            from app.models.database_models import User as DBUser
            from app.services.terms_service import requires_acceptance
            db = get_db()
            user = db.query(DBUser).filter_by(id=user_id).first()
            if user and requires_acceptance(user):
                return jsonify({
                    "error": "You must accept the Terms of Service before using the platform.",
                    "code": "terms_not_accepted",
                    "terms_url": "/api/terms/current",
                    "accept_url": "/api/terms/accept",
                }), 403
        except Exception:
            pass  # Do not block on middleware errors
        return None

    @app.before_request
    def _enforce_https_in_proxy_setups():
        """
        Enforce HTTPS for staging/production when requests accidentally arrive
        over HTTP.

        We rely on X-Forwarded-Proto when running behind nginx or another
        reverse proxy. For local development (ENV=local) this guard is a no-op.
        """
        env = str(app.config.get('ENV', 'local')).lower()
        if env == 'local':
            return None

        # Prefer proxy header when present, otherwise fall back to Flask scheme
        proto = request.headers.get('X-Forwarded-Proto', request.scheme or 'http').lower()
        if proto == 'http':
            # Upgrade to HTTPS while preserving host + path + query.
            url = request.url.replace('http://', 'https://', 1)
            # 308 keeps method/body for non-GET/HEAD.
            return redirect(url, code=308)

    print(f"Flask app template folder: {app.template_folder}")
    
    # Initialize Celery only when PDF ingestion uses Celery (production).
    # When USE_CELERY_FOR_INGESTION is False (e.g. local dev), no Redis/Celery worker is needed.
    if app.config.get('USE_CELERY_FOR_INGESTION', False):
        init_celery(app)
    else:
        print("Celery disabled for ingestion (USE_CELERY_FOR_INGESTION=false). PDF ingestion will run in-process.")

    # CLI: `flask db upgrade` — schema sync via init_db (no Flask-Migrate / Alembic chain).
    import click
    from flask.cli import with_appcontext

    @click.group(name="db")
    def db_cli_group():
        """Database commands (sync schema with init_db)."""

    @db_cli_group.command("upgrade")
    @with_appcontext
    def db_upgrade_command():
        from flask import current_app
        from app.utils.db import init_db

        init_db(current_app)
        click.echo("Database schema sync finished (init_db).")

    app.cli.add_command(db_cli_group)

    return app




