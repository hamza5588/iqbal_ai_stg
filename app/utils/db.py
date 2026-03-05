import logging
from flask import current_app, g
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool, QueuePool
from typing import Dict, Any, Optional, List
import os

logger = logging.getLogger(__name__)

# Global engine and session factory
_engine = None
_session_factory = None

def get_engine():
    """Get or create SQLAlchemy engine."""
    global _engine
    if _engine is None:
        db_url = current_app.config['SQLALCHEMY_DATABASE_URI']
        engine_options = current_app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}).copy()
        
        # SQLite-specific optimizations
        if db_url.startswith('sqlite'):
            # Use StaticPool for in-memory, QueuePool otherwise
            if 'sqlite:///:memory:' in db_url:
                engine_options['poolclass'] = StaticPool
                engine_options['connect_args'] = {'check_same_thread': False}
            else:
                engine_options['poolclass'] = StaticPool
                engine_options['connect_args'] = {
                    'check_same_thread': False,
                    'timeout': 20.0
                }
        else:
            # MySQL/PostgreSQL - use QueuePool
            engine_options.setdefault('poolclass', QueuePool)
        
        _engine = create_engine(db_url, **engine_options)
        
        # Add SQLite-specific event listeners
        if db_url.startswith('sqlite'):
            @event.listens_for(_engine, "connect")
            def set_sqlite_pragma(dbapi_conn, connection_record):
                """Set SQLite pragmas for better concurrency."""
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA foreign_keys = ON")
                cursor.execute("PRAGMA journal_mode = WAL")
                cursor.execute("PRAGMA busy_timeout = 30000")
                cursor.close()
        
        logger.info(f"Database engine created for: {db_url.split('@')[-1] if '@' in db_url else db_url}")
    
    return _engine

def get_session_factory():
    """Get or create session factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))
    return _session_factory

def get_db():
    """Get database session (replaces old get_db function)."""
    if 'db' not in g:
        factory = get_session_factory()
        g.db = factory()
    return g.db

def close_db(e=None):
    """Close database session. On commit failure, rolls back and invalidates
    the connection so the pool discards it (avoids reusing broken connections).
    """
    db = g.pop('db', None)
    if db is not None:
        try:
            db.commit()
        except Exception as ex:
            logger.error(f"Error committing database session: {ex}")
            try:
                db.rollback()
            except Exception as rollback_ex:
                logger.error(f"Error during rollback: {rollback_ex}")
            # Invalidate the connection so the pool discards it
            try:
                conn = db.connection()
                conn.invalidate()
            except Exception:
                pass
        finally:
            db.close()


def update_token_usage(user_id: int, tokens_used: int) -> None:
    """Update the token usage for a user."""
    from app.models.database_models import UserTokenUsage
    from sqlalchemy import func, and_
    from datetime import date
    
    try:
        db = get_db()
        
        # Try to update existing record for today
        today = date.today()
        token_usage = db.query(UserTokenUsage).filter(
            and_(
                UserTokenUsage.user_id == user_id,
                UserTokenUsage.date == today
            )
        ).first()
        
        if token_usage:
            token_usage.tokens_used += tokens_used
            token_usage.last_updated = func.now()
        else:
            # Insert a new record
            token_usage = UserTokenUsage(
                user_id=user_id,
                date=today,
                tokens_used=tokens_used
            )
            db.add(token_usage)
        
        db.commit()
    except Exception as e:
        logger.error(f"Error updating token usage: {str(e)}")
        db.rollback()
        raise


# token usage functions 
def get_token_usage(user_id: int) -> Dict[str, Any]:
    """Get current token usage for a user."""
    from app.models.database_models import UserTokenUsage
    from sqlalchemy import func, and_, desc
    from datetime import date, timedelta
    
    try:
        db = get_db()
        today = date.today()
        
        # Get today's usage
        token_usage = db.query(UserTokenUsage).filter(
            and_(
                UserTokenUsage.user_id == user_id,
                UserTokenUsage.date == today
            )
        ).first()
        
        # Get historical usage (last 7 days)
        seven_days_ago = today - timedelta(days=7)
        history = db.query(UserTokenUsage).filter(
            and_(
                UserTokenUsage.user_id == user_id,
                UserTokenUsage.date >= seven_days_ago
            )
        ).order_by(desc(UserTokenUsage.date)).all()
        
        return {
            'today': {
                'tokens_used': token_usage.tokens_used if token_usage else 0,
                'last_updated': token_usage.last_updated.isoformat() if token_usage and token_usage.last_updated else None
            },
            'history': [
                {
                    'date': record.date.isoformat() if hasattr(record.date, 'isoformat') else str(record.date),
                    'tokens_used': record.tokens_used
                }
                for record in history
            ]
        }
    except Exception as e:
        logger.error(f"Error getting token usage: {str(e)}")
        raise

def record_token_reset(user_id: int, tokens_used: int, limit_reached: bool = False) -> None:
    """Record when a user's token counter is reset."""
    from app.models.database_models import TokenResetHistory
    
    try:
        db = get_db()
        reset_record = TokenResetHistory(
            user_id=user_id,
            tokens_used=tokens_used,
            was_limit_reached=limit_reached
        )
        db.add(reset_record)
        db.commit()
    except Exception as e:
        logger.error(f"Error recording token reset: {str(e)}")
        db.rollback()
        raise
# ----- >


def init_db(app):
    """Initialize the database schema using SQLAlchemy."""
    from app.models.database_models import (
        Base, User, Lesson, Conversation, ChatHistory, SurveyResponse,
        UserPrompt, UserDocument, UserTokenUsage, TokenResetHistory,
        LessonFAQ, LessonChatHistory, EmailVerificationToken, PasswordResetToken,
        RAGChunk, RAGThread, RAGPrompt, Coupon, CouponRedemption, GlobalPrompt,
        SystemSettings, UserSettings
    )
    from app.load_testing.models import (
        TestUserSet, TestUser, TestDocumentSet, TestDocument, LoadTestResult, LoadTestLog
    )
    from sqlalchemy import inspect
    import time
    
    try:
        with app.app_context():
            engine = get_engine()
            
            # Create all tables
            Base.metadata.create_all(bind=engine)
            logger.info("Database tables created/verified successfully")
            
            # Migration: Update existing lessons to have lesson_id if missing
            try:
                db = get_db()
                from app.models.database_models import Lesson, User
                
                # Check if lessons table exists and has data
                inspector = inspect(engine)
                if 'lessons' in inspector.get_table_names():
                    # Get lessons without lesson_id
                    existing_lessons = db.query(Lesson).filter(
                        (Lesson.lesson_id == None) | (Lesson.lesson_id == '')
                    ).all()
                    
                    for lesson in existing_lessons:
                        if not lesson.lesson_id:
                            # Generate lesson_id like L000001
                            lesson.lesson_id = f"L{lesson.id:06d}"
                        
                        if not lesson.original_content and lesson.content:
                            lesson.original_content = lesson.content
                        
                        if not lesson.version_number:
                            lesson.version_number = 1
                        
                        if not lesson.status:
                            lesson.status = 'finalized'
                    
                    db.commit()
                    logger.info(f"Migrated {len(existing_lessons)} existing lessons")
            except Exception as e:
                logger.warning(f"Migration warning: {str(e)}")
                db.rollback()
            
            # Migration: Add subscription fields to existing users
            try:
                db = get_db()
                inspector = inspect(engine)
                if 'users' in inspector.get_table_names():
                    # Check if subscription_tier column exists
                    columns = [col['name'] for col in inspector.get_columns('users')]
                    
                    if 'subscription_tier' not in columns:
                        logger.info("Adding subscription columns to users table...")
                        # For SQLite
                        if 'sqlite' in str(engine.url):
                            db.execute(text("ALTER TABLE users ADD COLUMN subscription_tier VARCHAR(50) DEFAULT 'free'"))
                            db.execute(text("ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(255)"))
                            db.execute(text("ALTER TABLE users ADD COLUMN stripe_subscription_id VARCHAR(255)"))
                            db.execute(text("ALTER TABLE users ADD COLUMN subscription_status VARCHAR(50)"))
                        else:
                            # For MySQL/PostgreSQL - use ALTER TABLE
                            db.execute(text("ALTER TABLE users ADD COLUMN subscription_tier VARCHAR(50) DEFAULT 'free'"))
                            db.execute(text("ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(255)"))
                            db.execute(text("ALTER TABLE users ADD COLUMN stripe_subscription_id VARCHAR(255)"))
                            db.execute(text("ALTER TABLE users ADD COLUMN subscription_status VARCHAR(50)"))
                        
                        # Update existing users to have free tier
                        db.execute(text("UPDATE users SET subscription_tier = 'free' WHERE subscription_tier IS NULL"))
                        db.commit()
                        logger.info("Subscription columns added successfully")
                    else:
                        # Ensure existing users have subscription_tier set
                        existing_users = db.query(User).filter(
                            (User.subscription_tier == None) | (User.subscription_tier == '')
                        ).all()
                        for user in existing_users:
                            user.subscription_tier = 'free'
                        db.commit()
                        if existing_users:
                            logger.info(f"Updated {len(existing_users)} users with default subscription tier")
            except Exception as e:
                logger.warning(f"Subscription migration warning: {str(e)}")
                db.rollback()
            
            # Run RAG migration (add new columns, create rag_chunks table)
            try:
                import sys
                proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                if proj_root not in sys.path:
                    sys.path.insert(0, proj_root)
                from migrations.rag_milvus_migration import run_migration
                run_migration(engine)
            except Exception as mig_e:
                logger.warning("RAG migration warning: %s", mig_e)

            # RAG vector store startup health check (production: fail if Milvus unreachable)
            try:
                env = os.getenv("ENV", "local").lower()
                use_chroma = os.getenv("USE_CHROMA_LOCAL", "false").lower() in ("true", "1")
                if env != "local" and not use_chroma:
                    from app.utils.rag_vectorstore import ensure_collection
                    ensure_collection()
                    logger.info("RAG vector store (Milvus) health check passed")
                elif env == "local" or use_chroma:
                    from app.utils.rag_vectorstore import ensure_collection
                    ensure_collection()
                    logger.info("RAG vector store (Chroma) health check passed")
            except Exception as vs_e:
                env = os.getenv("ENV", "local").lower()
                if env in ("staging", "production"):
                    logger.error("RAG vector store unavailable in production: %s", vs_e)
                    raise SystemExit(f"Startup failed: Milvus unreachable: {vs_e}")
                logger.warning("RAG vector store health check: %s", vs_e)

            logger.info("Database initialized successfully")

    except Exception as e:
        logger.error(f"Database initialization error: {str(e)}")
        raise

