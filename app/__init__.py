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
    
    # Initialize database and LangGraph Postgres checkpointer
    with app.app_context():
        init_db(app)
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
    
    # Register blueprints (import here to avoid circular imports)
    from app.routes.auth import bp as auth_bp
    from app.routes.chat import bp as chat_bp
    from app.routes.api_key import bp as api_key_bp
    from app.routes.files import bp as file_bp
    from app.routes.chatbot_routes import bp as chatbot_bp
    from app.routes.survey import bp as survey_bp
    from app.routes.lesson_routes import bp as lesson_bp
    from app.routes.rag_routes import bp as rag_bp
    from app.routes.subscription import bp as subscription_bp
    from app.routes.admin_routes import bp as admin_bp

    # Register blueprints with appropriate prefixes
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(chat_bp)
    app.register_blueprint(api_key_bp, url_prefix='/api-key')
    app.register_blueprint(file_bp)
    app.register_blueprint(chatbot_bp, url_prefix='/api')
    app.register_blueprint(survey_bp, url_prefix='/api')
    app.register_blueprint(lesson_bp, url_prefix='/api/lessons')
    app.register_blueprint(rag_bp, url_prefix='/api/rag')
    app.register_blueprint(subscription_bp, url_prefix='/subscription')
    app.register_blueprint(admin_bp)
    
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

    return app

