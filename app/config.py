import os
from dotenv import load_dotenv
from datetime import timedelta

# Load environment variables
load_dotenv()

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
    DATABASE_URL = os.getenv('DATABASE_URL','postgresql://myuser:mypassword@localhost:5432/mydatabase')
    
    # SQLAlchemy configuration
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,  # Verify connections before using
        'pool_recycle': 300,    # Recycle connections after 5 minutes
        'echo': False           # Set to True for SQL query logging
    }
    
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
    NOMIC_API_KEY = os.getenv('NOMIC_API_KEY', 'nk-7Em9YdxJJI09E4vXTxJ9VOC2zygDGWD9eGBYxDLuG0E')  # Replace with your Nomic API key
    
    # Server URL for generating absolute URLs in production
    # Set this to your production domain (e.g., 'https://iqbalai.com')
    SERVER_URL = os.getenv('SERVER_URL', "https://iqbalai.com")
    
    # File upload configuration
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB max file size 
    
    # Stripe configuration
    STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY', 'pk_test_51SdENKR3GHwhdSflEXLb8vuJCGAwrUsjAYOvpbviKHNfVEjSKDZrBFqS92bIt1GuXPyzRO8DzwsK2ZecfyV0hlCy00hS7JJVz4')
    STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', 'sk_test_51SdENKR3GHwhdSflTCjLB5h83eDa8G4oZOLySliNfIduAeizo10wrhOZsnlfsslD5530mboYRii8MdXLTFIVNQEQ003sVSsIS1')
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
    OPENAI_TEMPERATURE = float(os.getenv('OPENAI_TEMPERATURE', '0.7'))
    OPENAI_MAX_TOKENS = int(os.getenv('OPENAI_MAX_TOKENS', '1024'))
    OPENAI_TIMEOUT = int(os.getenv('OPENAI_TIMEOUT', '60'))
    
    # Groq Configuration
    GROQ_API_KEY = os.getenv('GROQ_API_KEY', '')
    GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
    GROQ_TEMPERATURE = float(os.getenv('GROQ_TEMPERATURE', '0.7'))
    GROQ_MAX_TOKENS = int(os.getenv('GROQ_MAX_TOKENS', '1024'))
    GROQ_TIMEOUT = int(os.getenv('GROQ_TIMEOUT', '60'))
    
    # vLLM Configuration
    VLLM_API_BASE = os.getenv('VLLM_API_BASE', 'http://69.28.92.113:8000/v1')
    VLLM_MODEL = os.getenv('VLLM_MODEL', 'Qwen/Qwen2.5-14B-Instruct')
    VLLM_TEMPERATURE = float(os.getenv('VLLM_TEMPERATURE', '0.7'))
    VLLM_MAX_TOKENS = int(os.getenv('VLLM_MAX_TOKENS', '1024'))
    VLLM_TIMEOUT = int(os.getenv('VLLM_TIMEOUT', '600'))
    
    # Celery Configuration
    CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    CELERY_ACCEPT_CONTENT = ['json']
    CELERY_TASK_SERIALIZER = 'json'
    CELERY_RESULT_SERIALIZER = 'json'
    CELERY_TIMEZONE = 'UTC'
    CELERY_TASK_TRACK_STARTED = True
    CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes
    CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes