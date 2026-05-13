import importlib
import logging
from flask import current_app, g
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool, QueuePool
from typing import Dict, Any, Optional, List
import os

logger = logging.getLogger(__name__)

# Global engine and session factory (per-process)
_engine = None
_engine_pid = None
_session_factory = None


def reset_db_engine():
    """Dispose engine/session factory so next DB use recreates fresh connections."""
    global _engine, _session_factory, _engine_pid
    try:
        if _session_factory is not None:
            _session_factory.remove()
    except Exception as ex:
        logger.warning(f"Error removing scoped session during reset: {ex}")
    try:
        if _engine is not None:
            _engine.dispose()
    except Exception as ex:
        logger.warning(f"Error disposing database engine during reset: {ex}")
    _session_factory = None
    _engine = None
    _engine_pid = None

def get_engine():
    """Get or create SQLAlchemy engine.

    IMPORTANT: the engine is kept per-process so that forking servers
    (e.g. gunicorn or Celery workers) do not share the same DBAPI
    connections across processes, which can corrupt PostgreSQL sessions.

    Because each worker process now has its own independent engine and
    connection pool, DB usage is naturally isolated per worker. This made
    our earlier experiment of separating Celery ingestion into its own
    queue/worker (to reduce DB pressure) unnecessary, so that queue split
    was rolled back.
    """
    global _engine, _engine_pid

    current_pid = os.getpid()

    # If this is a new process compared to when the engine was created,
    # dispose the old engine so a fresh connection pool is created.
    if _engine is not None and _engine_pid is not None and _engine_pid != current_pid:
        try:
            _engine.dispose()
            logger.info("Disposed SQLAlchemy engine after fork (pid changed)")
        except Exception as ex:
            logger.warning(f"Error disposing engine after fork: {ex}")
        finally:
            _engine = None

    if _engine is None:
        db_url = current_app.config['SQLALCHEMY_DATABASE_URI']
        engine_options = current_app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}).copy()
        
        # SQLite-specific optimizations
        if db_url.startswith('sqlite'):
            # StaticPool does not accept pool_size/max_overflow — strip them
            engine_options.pop('pool_size', None)
            engine_options.pop('max_overflow', None)
            engine_options.pop('pool_pre_ping', None)
            # Use StaticPool for in-memory, QueuePool otherwise
            if ':memory:' in db_url:
                engine_options['poolclass'] = StaticPool
                engine_options['connect_args'] = {'check_same_thread': False}
            else:
                engine_options['poolclass'] = StaticPool
                engine_options['connect_args'] = {
                    'check_same_thread': False,
                    'timeout': 20.0
                }
        else:
            # MySQL/PostgreSQL - use QueuePool and enable pool_pre_ping
            engine_options.setdefault('poolclass', QueuePool)
            engine_options.setdefault('pool_pre_ping', True)
        
        _engine = create_engine(db_url, **engine_options)
        _engine_pid = current_pid
        
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
        
        logger.info(f"Database engine created for: {db_url.split('@')[-1] if '@' in db_url else db_url} (pid={current_pid})")
    
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
    # School hierarchy + learning delivery (roster, quiz sessions, lecture links).
    # Use importlib so `import app.models...` does not rebind the name `app` (Flask app).
    importlib.import_module("app.models.school_org_models")
    importlib.import_module("app.models.school_learning_models")
    # Phase 1 syllabus / exam types (FK targets for phase3)
    importlib.import_module("app.models.phase1_models")
    # Phase 3 student learning
    importlib.import_module("app.models.phase3_models")
    # Phase 4 intelligence, groups, VA
    importlib.import_module("app.models.phase4_models")
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

            # PostgreSQL: widen users.role CHECK constraint for school participant roles
            try:
                if engine.dialect.name == "postgresql":
                    db_pg = get_db()
                    insp_pg = inspect(engine)
                    if "users" in insp_pg.get_table_names():
                        db_pg.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS check_user_role"))
                        db_pg.execute(
                            text(
                                "ALTER TABLE users ADD CONSTRAINT check_user_role CHECK (role IN ("
                                "'student','teacher','admin','principal','coordinator','school_admin',"
                                "'district_admin','platform_admin','parent'))"
                            )
                        )
                        db_pg.commit()
                        logger.info("PostgreSQL: users.check_user_role updated for school roles")
            except Exception as pg_role_e:
                logger.warning("PostgreSQL role constraint migration: %s", pg_role_e)
                try:
                    get_db().rollback()
                except Exception:
                    pass
            
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
            
            # Migration: Ensure load_test_user_sets has set_prompt column
            try:
                db = get_db()
                inspector = inspect(engine)
                if 'load_test_user_sets' in inspector.get_table_names():
                    columns = [col['name'] for col in inspector.get_columns('load_test_user_sets')]
                    if 'set_prompt' not in columns:
                        logger.info("Adding set_prompt column to load_test_user_sets table...")
                        # Works for SQLite and Postgres/MySQL; TEXT is portable here
                        db.execute(text("ALTER TABLE load_test_user_sets ADD COLUMN set_prompt TEXT"))
                        db.commit()
                        logger.info("set_prompt column added successfully")
            except Exception as e:
                logger.warning(f"Load testing migration warning: {str(e)}")
                db.rollback()
            
            # Migration: Phase 1 user columns (must run before any ORM query on User that
            # selects these attributes). create_all does not ALTER existing tables.
            try:
                db = get_db()
                inspector = inspect(engine)
                if "users" in inspector.get_table_names():
                    user_cols = {c["name"] for c in inspector.get_columns("users")}
                    dialect = engine.dialect.name
                    stmts = []
                    added_names = []
                    if "is_active" not in user_cols:
                        if dialect == "sqlite":
                            stmts.append(
                                "ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"
                            )
                        else:
                            stmts.append(
                                "ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true"
                            )
                        added_names.append("is_active")
                    if "preferred_language" not in user_cols:
                        if dialect == "sqlite":
                            stmts.append(
                                "ALTER TABLE users ADD COLUMN preferred_language VARCHAR(10) NOT NULL DEFAULT 'en'"
                            )
                        else:
                            stmts.append(
                                "ALTER TABLE users ADD COLUMN preferred_language VARCHAR(10) NOT NULL DEFAULT 'en'"
                            )
                        added_names.append("preferred_language")
                    if "personality_id" not in user_cols:
                        stmts.append(
                            "ALTER TABLE users ADD COLUMN personality_id INTEGER"
                        )
                        added_names.append("personality_id")
                    if "suspended_at" not in user_cols:
                        if dialect == "sqlite":
                            stmts.append(
                                "ALTER TABLE users ADD COLUMN suspended_at DATETIME"
                            )
                        else:
                            stmts.append(
                                "ALTER TABLE users ADD COLUMN suspended_at TIMESTAMP"
                            )
                        added_names.append("suspended_at")
                    if "suspended_by_id" not in user_cols:
                        stmts.append(
                            "ALTER TABLE users ADD COLUMN suspended_by_id INTEGER"
                        )
                        added_names.append("suspended_by_id")
                    if "terms_accepted_version" not in user_cols:
                        stmts.append(
                            "ALTER TABLE users ADD COLUMN terms_accepted_version VARCHAR(32)"
                        )
                        added_names.append("terms_accepted_version")
                    for sql in stmts:
                        db.execute(text(sql))
                    if stmts:
                        db.commit()
                        logger.info(
                            "Added missing Phase 1 columns on users: %s",
                            added_names,
                        )
            except Exception as e:
                logger.warning("Phase 1 users column migration warning: %s", e)
                try:
                    get_db().rollback()
                except Exception:
                    pass
            
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
            
            # Migration: Phase 3 student_owned_uploads OCR text column
            try:
                db = get_db()
                inspector = inspect(engine)
                if "student_owned_uploads" in inspector.get_table_names():
                    cols = {c["name"] for c in inspector.get_columns("student_owned_uploads")}
                    if "ocr_extracted_text" not in cols:
                        logger.info("Adding ocr_extracted_text column to student_owned_uploads...")
                        db.execute(text("ALTER TABLE student_owned_uploads ADD COLUMN ocr_extracted_text TEXT"))
                        db.commit()
                        logger.info("ocr_extracted_text column added successfully")
            except Exception as e:
                logger.warning("student_owned_uploads OCR migration warning: %s", str(e))
                try:
                    get_db().rollback()
                except Exception:
                    pass

            # Migration: Phase 3 calendar sync metadata + reminder dedupe state
            try:
                db = get_db()
                inspector = inspect(engine)
                if "user_calendar_connections" in inspector.get_table_names():
                    cols = {c["name"] for c in inspector.get_columns("user_calendar_connections")}
                    if "sync_meta_json" not in cols:
                        logger.info("Adding sync_meta_json to user_calendar_connections...")
                        db.execute(
                            text("ALTER TABLE user_calendar_connections ADD COLUMN sync_meta_json TEXT")
                        )
                        db.commit()
                if "student_learning_preferences" in inspector.get_table_names():
                    cols = {c["name"] for c in inspector.get_columns("student_learning_preferences")}
                    if "reminder_state_json" not in cols:
                        logger.info("Adding reminder_state_json to student_learning_preferences...")
                        db.execute(
                            text(
                                "ALTER TABLE student_learning_preferences ADD COLUMN reminder_state_json TEXT"
                            )
                        )
                        db.commit()
            except Exception as e:
                logger.warning("Phase 3 reminder/calendar column migration warning: %s", e)
                try:
                    get_db().rollback()
                except Exception:
                    pass

            # Phase 4: allow extended notification types (drop legacy CHECK on Postgres)
            try:
                dbn = get_db()
                if engine.dialect.name == "postgresql":
                    dbn.execute(
                        text("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS check_notification_type")
                    )
                    dbn.commit()
            except Exception as nchk_e:
                logger.warning("notifications type constraint migration: %s", nchk_e)
                try:
                    get_db().rollback()
                except Exception:
                    pass

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

