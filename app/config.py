import os
from dotenv import load_dotenv
from datetime import timedelta

# Load environment variables (project root .env, then app/utils/.env so both are applied)
load_dotenv()
_load_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'utils', '.env')
if os.path.isfile(_load_env_path):
    load_dotenv(_load_env_path, override=True)

class Config:
    # Basic Flask configuration
    SECRET_KEY = os.getenv('SECRET_KEY', 'your_secret_key')  # Change this in production
    
    # Email configuration
    # MAIL_SERVER = 'mail.privateemail.com'
    # MAIL_PORT = 465
    # # MAIL_SERVER = 'smtp.gmail.com'
    # # MAIL_PORT = 587
    # MAIL_USE_TLS = True
    # MAIL_USE_SSL = False
    # # MAIL_USERNAME = 'info@iqbalai.com'  # Make sure this is the full email address
    # # MAIL_PASSWORD = 'Iqbalai12@'  # Make sure this is the correct app-specific password
    # # MAIL_DEFAULT_SENDER = 'info@iqbalai.com'
    # MAIL_USERNAME = os.getenv('MAIL_USERNAME', 'info@iqbalai.com')
    # MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', 'Iqbalai12@')
    # MAIL_DEFAULT_SENDER = os.getenv('MAIL_USERNAME', 'info@iqbalai.com')
    # MAIL_DEBUG = True  # Enable debug mode to see more detailed logs

        # Email configuration with extended timeout
    MAIL_SERVER = 'mail.privateemail.com'
    MAIL_PORT = 465
    MAIL_USE_TLS = False
    MAIL_USE_SSL = True
    MAIL_USERNAME = os.getenv('MAIL_USERNAME', 'info@iqbalai.com')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', 'Iqbalai123@')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_USERNAME', 'info@iqbalai.com')
    MAIL_DEBUG = True
    MAIL_MAX_EMAILS = None
    MAIL_ASCII_ATTACHMENTS = False
    MAIL_TIMEOUT = 30  # Add 30 second timeout
    # Database configuration - supports SQLite, MySQL, and PostgreSQL
    # Examples:
    # SQLite: sqlite:///instance/chatbot.db or sqlite:////absolute/path/to/chatbot.db
    # MySQL: mysql+pymysql://user:password@localhost/dbname
    # PostgreSQL: postgresql://user:password@localhost/dbname or postgresql+psycopg2://user:password@localhost/dbname
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://myuser:mypassword@localhost:5432/mydatabase')    
    # SQLAlchemy configuration
    if DATABASE_URL.startswith('sqlite'):
        # Resolve to absolute path and create parent dir so SQLite can open the file
        _db_path = DATABASE_URL.replace('sqlite:///', '').replace('sqlite:////', '')
        if not os.path.isabs(_db_path):
            _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _db_path = os.path.join(_project_root, _db_path)
        _db_dir = os.path.dirname(_db_path)
        if _db_dir and not os.path.isdir(_db_dir):
            os.makedirs(_db_dir, exist_ok=True)
        # Use three slashes for absolute path (Windows: sqlite:///C:/path; Unix: sqlite:////path)
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.normpath(_db_path).replace('\\', '/')
    else:
        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,   # Verify connections before using
        'pool_recycle': 300,     # Recycle connections after 5 minutes
        'echo': False,           # Set to True for SQL query logging
        'pool_size': 3,          # Bounded core pool size (see technical audit Issue 4)
        'max_overflow': 3,       # Bounded overflow connections per process
    }
    if not DATABASE_URL.startswith('sqlite'):
        SQLALCHEMY_ENGINE_OPTIONS['pool_size'] = 5
        SQLALCHEMY_ENGINE_OPTIONS['max_overflow'] = 5
    
    # Legacy DATABASE path for backward compatibility (used only if needed)
    if DATABASE_URL.startswith('sqlite'):
        # Extract path from SQLite URL
        db_path = DATABASE_URL.replace('sqlite:///', '').replace('sqlite:////', '')
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), db_path)
        DATABASE = db_path
    else:
        DATABASE = None  # Not used for non-SQLite databases
    
    # Nomic API configuration
    NOMIC_API_KEY = os.getenv('NOMIC_API_KEY', '')  # Set via environment variable
    
    # Server URL for generating absolute URLs in production
    # Set this to your production domain (e.g., 'https://iqbalai.com')
    SERVER_URL = os.getenv('SERVER_URL', "https://iqbalai.com")
    
    # File upload configuration
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB max file size 
    
    # Stripe configuration
    STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY', '')                                                                                                                                                    
    STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')         
    STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
    
    # Stripe Product IDs (we'll fetch the default price from these)
    STRIPE_PRO_PRODUCT_ID = os.getenv('STRIPE_PRO_PRODUCT_ID', 'prod_TaPSD7B5zRAeKb')
    STRIPE_PRO_PLUS_PRODUCT_ID = os.getenv('STRIPE_PRO_PLUS_PRODUCT_ID', 'prod_TaPShi2ENfvmO3')
    
    # LLM Provider Configuration
    # Set LLM_PROVIDER to 'openai' or 'vllm' to switch between providers
    LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'openai').lower()  # Default to OpenAI
    
    # OpenAI Configuration
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
    OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-3.5-turbo')
    OPENAI_TEMPERATURE = float(os.getenv('OPENAI_TEMPERATURE', '0.5'))
    OPENAI_MAX_TOKENS = int(os.getenv('OPENAI_MAX_TOKENS', '1024'))
    OPENAI_TIMEOUT = int(os.getenv('OPENAI_TIMEOUT', '60'))
    
    # Groq Configuration
    GROQ_API_KEY = os.getenv('GROQ_API_KEY', '')
    GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
    GROQ_TEMPERATURE = float(os.getenv('GROQ_TEMPERATURE', '0.5'))
    GROQ_MAX_TOKENS = int(os.getenv('GROQ_MAX_TOKENS', '1024'))
    GROQ_TIMEOUT = int(os.getenv('GROQ_TIMEOUT', '60'))
    
    # vLLM Configuration
    VLLM_API_BASE = os.getenv('VLLM_API_BASE', 'http://69.28.92.113:8000/v1')
    VLLM_MODEL = os.getenv('VLLM_MODEL', 'Qwen/Qwen2.5-14B-Instruct')
    VLLM_TEMPERATURE = float(os.getenv('VLLM_TEMPERATURE', '0.5'))
    VLLM_MAX_TOKENS = int(os.getenv('VLLM_MAX_TOKENS', '1024'))
    VLLM_TIMEOUT = int(os.getenv('VLLM_TIMEOUT', '600'))
    
    # ENV: local | staging | production. local=Chroma, else=Milvus
    ENV = os.getenv('ENV', 'local').lower()
    USE_CHROMA_LOCAL = os.getenv('USE_CHROMA_LOCAL', 'false').lower() in ('true', '1')

    # Milvus & RAG Embeddings Configuration (HuggingFace only, 384 dims)
    MILVUS_HOST = os.getenv('MILVUS_HOST', 'localhost')
    MILVUS_PORT = int(os.getenv('MILVUS_PORT', '19530'))
    EMBEDDING_MODEL_NAME = os.getenv('EMBEDDING_MODEL_NAME', 'sentence-transformers/all-MiniLM-L6-v2')
    EMBEDDING_DIM = int(os.getenv('EMBEDDING_DIM', '384'))

    # Celery Configuration
    # Set USE_CELERY_FOR_INGESTION=true in production/staging so PDF ingestion runs in Celery workers.
    # Upload then returns immediately with task_id; client polls for status. Avoids gateway timeouts (504)
    # and keeps web workers free for login/chat. Set to false for local dev (no Redis/Celery needed).
    USE_CELERY_FOR_INGESTION = os.getenv('USE_CELERY_FOR_INGESTION', 'false').lower() in ('true', '1')
    CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    CELERY_ACCEPT_CONTENT = ['json']
    CELERY_TASK_SERIALIZER = 'json'
    CELERY_RESULT_SERIALIZER = 'json'
    CELERY_TIMEZONE = 'UTC'
    CELERY_TASK_TRACK_STARTED = True
    CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes
    CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes

    # Temporary upload directory for PDF ingestion when using Celery.
    # This path is shared between the web app and Celery workers via Docker volumes.
    # Use system temp by default to avoid read-only bind mounts (e.g. /app in some containers).
    UPLOAD_TEMP_DIR = os.getenv('UPLOAD_TEMP_DIR', os.path.join('/tmp', 'iqbalai_uploads'))
    RAG_LARGE_DOC_THRESHOLD_MB = int(os.getenv('RAG_LARGE_DOC_THRESHOLD_MB', '40'))
    RAG_INGEST_STANDARD_QUEUE = os.getenv('RAG_INGEST_STANDARD_QUEUE', 'ingest')
    RAG_INGEST_LARGE_QUEUE = os.getenv('RAG_INGEST_LARGE_QUEUE', 'ingest_large')
    # Increased defaults to better tolerate heavy PDFs and staging load spikes.
    RAG_INGEST_STANDARD_SOFT_TIME_LIMIT = int(os.getenv('RAG_INGEST_STANDARD_SOFT_TIME_LIMIT', '2400'))
    RAG_INGEST_STANDARD_TIME_LIMIT = int(os.getenv('RAG_INGEST_STANDARD_TIME_LIMIT', '2700'))
    RAG_INGEST_LARGE_SOFT_TIME_LIMIT = int(os.getenv('RAG_INGEST_LARGE_SOFT_TIME_LIMIT', '5400'))
    RAG_INGEST_LARGE_TIME_LIMIT = int(os.getenv('RAG_INGEST_LARGE_TIME_LIMIT', '6000'))
    RAG_INGEST_STANDARD_STREAM_TIMEOUT = int(os.getenv('RAG_INGEST_STANDARD_STREAM_TIMEOUT', '600'))
    RAG_INGEST_LARGE_STREAM_TIMEOUT = int(os.getenv('RAG_INGEST_LARGE_STREAM_TIMEOUT', '1800'))

    # -------------------
    # RAG load-test flags
    # -------------------
    # Use `LOAD_TEST_MODE=true` in staging/load testing to reduce concurrency pressure.
    LOAD_TEST_MODE = os.getenv('LOAD_TEST_MODE', 'false').lower() in ('true', '1', 'yes')

    # Enable/disable headings extraction completely.
    # Normal users should keep headings enabled by default.
    ENABLE_RAG_HEADINGS = os.getenv('ENABLE_RAG_HEADINGS', 'true').lower() in ('true', '1', 'yes')

    # When load testing, delay headings extraction so it doesn't contend with ingestion.
    DELAY_RAG_HEADINGS_FOR_LOAD_TEST = os.getenv(
        'DELAY_RAG_HEADINGS_FOR_LOAD_TEST',
        'false' if (LOAD_TEST_MODE or ENV == 'staging') else 'false'
    ).lower() in ('true', '1', 'yes')

    # Optional delay seconds before starting headings extraction in load-test mode.
    RAG_HEADINGS_DELAY_SECONDS = int(os.getenv('RAG_HEADINGS_DELAY_SECONDS', '30'))

    # Disable file-based debug logging in staging/load-test mode because it can be costly under concurrency.
    _ENV = os.getenv('ENV', 'local').lower()
    ENABLE_RAG_DEBUG_FILE_LOGS = os.getenv(
        'ENABLE_RAG_DEBUG_FILE_LOGS',
        'false' if (LOAD_TEST_MODE or _ENV == 'staging') else 'true'
    ).lower() in ('true', '1', 'yes')

    # Control embedding batch parallelism defaults.
    # NOTE: Actual effective default is also enforced in `app/utils/rag_service.py`.
    RAG_EMBED_PARALLEL_BATCHES_LOAD_TEST_DEFAULT = int(os.getenv('RAG_EMBED_PARALLEL_BATCHES_LOAD_TEST_DEFAULT', '1'))

    _allowed_origins_raw = os.getenv('ALLOWED_ORIGINS', '')
    if _allowed_origins_raw.strip():
        ALLOWED_ORIGINS = [o.strip() for o in _allowed_origins_raw.split(',') if o.strip()]
    else:
        ALLOWED_ORIGINS = [
            'http://localhost:3000', 'http://localhost:8080', 'http://localhost:5000',
            'http://localhost:5002', 'http://127.0.0.1:3000', 'http://127.0.0.1:8080',
            'http://127.0.0.1:5000', 'http://127.0.0.1:5002',
        ]
    EMBED_CLIENT_KEYS = os.getenv('EMBED_CLIENT_KEYS', '')
    EMBED_RATE_LIMIT_PER_HOUR = int(os.getenv('EMBED_RATE_LIMIT_PER_HOUR', '60'))
    EMBED_DEFAULT_OWNER_EMAIL = os.getenv('EMBED_DEFAULT_OWNER_EMAIL', '')