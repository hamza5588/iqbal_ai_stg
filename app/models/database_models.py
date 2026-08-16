"""SQLAlchemy database models for the application"""
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, Date, ForeignKey,
    CheckConstraint, UniqueConstraint, Index, func, Numeric,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class User(Base):
    """User model"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(255), nullable=False, unique=True)
    useremail = Column(String(255), nullable=False, unique=True)
    password = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default='student',
                  server_default='student')
    class_standard = Column(String(100), nullable=False)
    medium = Column(String(100), nullable=False)
    groq_api_key = Column(Text, nullable=False)
    subscription_tier = Column(String(50), nullable=False, default='free', server_default='free')
    stripe_customer_id = Column(String(255), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    subscription_status = Column(String(50), nullable=True)  # active, canceled, past_due, etc.
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    last_login = Column(DateTime, nullable=True)
    
    # Relationships
    lessons = relationship("Lesson", back_populates="teacher", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    survey_responses = relationship("SurveyResponse", back_populates="user", cascade="all, delete-orphan")
    user_prompts = relationship("UserPrompt", back_populates="user", cascade="all, delete-orphan")
    user_documents = relationship("UserDocument", back_populates="user", cascade="all, delete-orphan")
    token_usage = relationship("UserTokenUsage", back_populates="user", cascade="all, delete-orphan")
    token_reset_history = relationship("TokenResetHistory", back_populates="user", cascade="all, delete-orphan")
    rag_threads = relationship("RAGThread", back_populates="user", cascade="all, delete-orphan")
    llm_usage_events = relationship("LLMUsageEvent", back_populates="user")

    __table_args__ = (
        CheckConstraint("role IN ('student', 'teacher', 'admin')", name='check_user_role'),
        CheckConstraint("subscription_tier IN ('free', 'pro', 'pro_plus')", name='check_subscription_tier'),
    )


class Lesson(Base):
    """Lesson model"""
    __tablename__ = 'lessons'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    teacher_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    title = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    detailed_answer = Column(Text, nullable=True)
    learning_objectives = Column(Text, nullable=True)
    focus_area = Column(String(255), nullable=True)
    grade_level = Column(String(100), nullable=True)
    content = Column(Text, nullable=False)
    file_name = Column(String(255), nullable=True)
    rag_thread_id = Column(String(255), nullable=True)  # RAG thread for PDF retrieval (student ask question)
    conversation_id = Column(Integer, nullable=True)  # DB conversation linked to this lesson's chat session
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, 
                       server_default=func.now(), server_onupdate=func.now())
    is_public = Column(Boolean, default=True, server_default='1')
    has_child_version = Column(Boolean, default=False, server_default='0')
    parent_lesson_id = Column(Integer, ForeignKey('lessons.id', ondelete='CASCADE'), nullable=True)
    version = Column(Integer, default=1, server_default='1')
    
    # Versioning fields
    lesson_id = Column(String(100), nullable=True)  # Logical lesson identifier
    version_number = Column(Integer, default=1, server_default='1')
    parent_version_id = Column(Integer, ForeignKey('lessons.id', ondelete='CASCADE'), nullable=True)
    original_content = Column(Text, nullable=True)
    draft_content = Column(Text, nullable=True)
    status = Column(String(50), default='finalized', server_default='finalized')
    
    # Relationships
    teacher = relationship("User", back_populates="lessons")
    parent_lesson = relationship("Lesson", remote_side=[id], foreign_keys=[parent_lesson_id])
    parent_version = relationship("Lesson", remote_side=[id], foreign_keys=[parent_version_id])
    
    __table_args__ = (
        UniqueConstraint('lesson_id', 'version_number', name='idx_lesson_version_unique'),
        Index('idx_lessons_teacher_id', 'teacher_id'),
        Index('idx_lessons_grade_level', 'grade_level'),
        Index('idx_lessons_focus_area', 'focus_area'),
        Index('idx_lessons_is_public', 'is_public'),
    )


class Conversation(Base):
    """Conversation model"""
    __tablename__ = 'conversations'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    title = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, server_default='1')
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                       server_default=func.now(), server_onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="conversations")
    chat_history = relationship("ChatHistory", back_populates="conversation", 
                               cascade="all, delete-orphan")
    summaries = relationship("ConversationSummary", back_populates="conversation", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_conversations_user_id', 'user_id'),
    )


class ChatHistory(Base):
    """Chat history model"""
    __tablename__ = 'chat_history'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey('conversations.id', ondelete='CASCADE'), 
                            nullable=False)
    message = Column(Text, nullable=False)
    role = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    
    # Relationships
    conversation = relationship("Conversation", back_populates="chat_history")
    
    __table_args__ = (
        CheckConstraint("role IN ('user', 'bot')", name='check_chat_role'),
        Index('idx_chat_history_conversation_id', 'conversation_id'),
    )


class ConversationSummary(Base):
    """Conversation summary model"""
    __tablename__ = 'conversation_summaries'

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(
        Integer,
        ForeignKey('conversations.id', ondelete='CASCADE'),
        nullable=False,
    )
    lesson_id = Column(
        Integer,
        ForeignKey('lessons.id', ondelete='SET NULL'),
        nullable=True,
    )
    summary_text = Column(Text, nullable=False)
    generated_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False)
    last_message_id = Column(Integer, nullable=True)
    last_message_timestamp = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
        nullable=False,
    )

    conversation = relationship("Conversation", back_populates="summaries")

    __table_args__ = (
        Index('idx_conversation_summaries_conversation_id', 'conversation_id'),
        Index('idx_conversation_summaries_lesson_id', 'lesson_id'),
        Index('idx_conversation_summaries_generated_at', 'generated_at'),
        Index('idx_conversation_summaries_last_message_id', 'last_message_id'),
        Index('idx_conversation_summaries_last_message_timestamp', 'last_message_timestamp'),
    )


class SurveyResponse(Base):
    """Survey response model"""
    __tablename__ = 'survey_responses'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    rating = Column(Integer, nullable=False)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    
    # Relationships
    user = relationship("User", back_populates="survey_responses")
    
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 10", name='check_rating_range'),
    )


class UserPrompt(Base):
    """User prompt model"""
    __tablename__ = 'user_prompts'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    prompt = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                       server_default=func.now(), server_onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="user_prompts")
    
    __table_args__ = (
        Index('idx_user_prompts_user_id', 'user_id'),
    )


class UserDocument(Base):
    """User document model for RAG PDF ingestion"""
    __tablename__ = 'user_documents'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    thread_id = Column(String(255), ForeignKey('rag_threads.thread_id', ondelete='CASCADE'), nullable=True)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False)
    file_type = Column(String(100), nullable=False)
    vector_db_ids = Column(Text, nullable=True)  # deprecated; kept for backward compat
    processed = Column(Boolean, default=False, server_default='0')
    processing_status = Column(String(50), default='uploaded', server_default='uploaded')  # uploaded|processing|processed|failed
    last_error = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    last_accessed_at = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="user_documents")
    rag_thread = relationship("RAGThread", back_populates="user_documents")
    
    __table_args__ = (
        Index('idx_user_documents_user_id', 'user_id'),
        Index('idx_user_documents_file_type', 'file_type'),
        Index('idx_user_documents_thread_id', 'thread_id'),
        Index('idx_user_documents_processing_status', 'processing_status'),
    )


class UserTokenUsage(Base):
    """User token usage model"""
    __tablename__ = 'user_token_usage'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    date = Column(Date, nullable=False, default=datetime.utcnow().date, 
                  server_default=func.current_date())
    tokens_used = Column(Integer, nullable=False, default=0, server_default='0')
    last_updated = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    
    # Relationships
    user = relationship("User", back_populates="token_usage")
    
    __table_args__ = (
        UniqueConstraint('user_id', 'date', name='unique_user_date'),
        Index('idx_user_token_usage_user_id', 'user_id'),
        Index('idx_user_token_usage_date', 'date'),
    )


class TokenResetHistory(Base):
    """Token reset history model"""
    __tablename__ = 'token_reset_history'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    reset_time = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    tokens_used = Column(Integer, nullable=False)
    was_limit_reached = Column(Boolean, default=False, server_default='0')
    
    # Relationships
    user = relationship("User", back_populates="token_reset_history")


class LessonFAQ(Base):
    """Lesson FAQ model"""
    __tablename__ = 'lesson_faq'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    lesson_id = Column(Integer, nullable=False)  # References lessons.id
    question = Column(Text, nullable=False)
    count = Column(Integer, default=1, server_default='1')
    canonical_question = Column(Text, nullable=True)


class LessonChatHistory(Base):
    """Lesson chat history model"""
    __tablename__ = 'lesson_chat_history'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    lesson_id = Column(Integer, nullable=False)  # References lessons.id
    user_id = Column(Integer, nullable=False)  # References users.id
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    canonical_question = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())


class EmailVerificationToken(Base):
    """Email verification token model"""
    __tablename__ = 'email_verification_tokens'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(255), nullable=False, unique=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    used = Column(Boolean, default=False, server_default='0')
    
    __table_args__ = (
        Index('idx_email_verification_token', 'token'),
        Index('idx_email_verification_email', 'email'),
    )


class PasswordResetToken(Base):
    """Password reset token model"""
    __tablename__ = 'password_reset_tokens'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, index=True)
    otp = Column(String(10), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    used = Column(Boolean, default=False, server_default='0')
    
    __table_args__ = (
        Index('idx_password_reset_email', 'email'),
    )


class RAGChunk(Base):
    """RAG chunk model - PostgreSQL stores chunk text. Vector DB stores vectors only."""
    __tablename__ = 'rag_chunks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String(255), ForeignKey('rag_threads.thread_id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey('user_documents.id', ondelete='SET NULL'), nullable=True)
    chunk_index = Column(Integer, nullable=False)
    page = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    source = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    thread = relationship("RAGThread", back_populates="rag_chunks")

    __table_args__ = (
        Index('idx_rag_chunk_thread_user', 'thread_id', 'user_id'),
        Index('idx_rag_chunk_page', 'thread_id', 'user_id', 'page'),
    )


class RAGHeading(Base):
    """RAG heading model - stores extracted headings/topics for a thread."""
    __tablename__ = 'rag_headings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String(255), ForeignKey('rag_threads.thread_id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    page = Column(Integer, nullable=True)
    heading = Column(String(512), nullable=False)
    normalized_heading = Column(String(512), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    thread = relationship("RAGThread", back_populates="rag_headings")

    __table_args__ = (
        Index('idx_rag_headings_thread_user', 'thread_id', 'user_id'),
        Index('idx_rag_headings_normalized', 'normalized_heading'),
    )


class RAGThread(Base):
    """RAG Thread model for storing PDF chat threads"""
    __tablename__ = 'rag_threads'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    thread_id = Column(String(255), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=True)
    has_document = Column(Boolean, default=False, server_default='0')
    doc_count = Column(Integer, default=0, server_default='0')
    num_pages = Column(Integer, nullable=True)
    last_ingested_at = Column(DateTime, nullable=True)
    embedding_model = Column(String(255), nullable=True)
    embedding_dim = Column(Integer, nullable=True)
    lesson_finalized = Column(Boolean, default=False, server_default='0')
    last_lesson_text = Column(Text, nullable=True)
    lesson_title = Column(String(512), nullable=True)
    headings_ready = Column(Boolean, default=False, server_default='0')
    headings_count = Column(Integer, default=0, server_default='0')
    headings_last_scanned_at = Column(DateTime, nullable=True)
    # General-knowledge consent state machine (Phase 4): tracks the single outstanding
    # "answer from general knowledge?" offer for this thread, if any. 'none' | 'offered' |
    # 'granted' | 'denied'. Single-use per question - see app/utils/gk_consent.py for the
    # pure transition logic and app/utils/rag_service.py for where it's read/written.
    gk_consent_state = Column(String(16), nullable=False, default='none', server_default='none')
    gk_consent_question = Column(Text, nullable=True)
    gk_consent_updated_at = Column(DateTime, nullable=True)
    # Terminal ingestion state: 'pending' | 'processing' | 'success' | 'failed'.
    # Lets the chat endpoint distinguish "still working" from "will never finish"
    # instead of returning the same generic message for both.
    ingest_status = Column(String(20), nullable=True)
    ingest_error = Column(Text, nullable=True)
    # Set to now()+time_limit when a Celery ingest task is queued. If a task is
    # hard-killed (SIGKILL on Celery's time_limit) it never reaches its own
    # except block, so this deadline is how we detect "abandoned" ingestion.
    ingest_deadline_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="rag_threads")
    rag_prompts = relationship("RAGPrompt", back_populates="thread", cascade="all, delete-orphan")
    user_documents = relationship("UserDocument", back_populates="rag_thread")
    rag_chunks = relationship("RAGChunk", back_populates="thread", cascade="all, delete-orphan")
    rag_headings = relationship("RAGHeading", back_populates="thread", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_rag_thread_user_id', 'user_id'),
        Index('idx_rag_thread_thread_id', 'thread_id'),
        Index('idx_rag_thread_has_document', 'has_document'),
    )


class RAGPrompt(Base):
    """RAG Prompt model for storing custom system prompts for RAG threads"""
    __tablename__ = 'rag_prompts'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    thread_id = Column(String(255), ForeignKey('rag_threads.thread_id', ondelete='CASCADE'), nullable=True, index=True)
    prompt = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                       server_default=func.now(), server_onupdate=func.now())
    
    # Relationships
    user = relationship("User")
    thread = relationship("RAGThread", back_populates="rag_prompts")
    
    __table_args__ = (
        Index('idx_rag_prompts_user_id', 'user_id'),
        Index('idx_rag_prompts_thread_id', 'thread_id'),
    )


class GlobalPrompt(Base):
    """Global system prompt model (applies to all users)"""
    __tablename__ = 'global_prompts'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                       server_default=func.now(), server_onupdate=func.now())
    updated_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)


class Coupon(Base):
    """Coupon model for subscription coupons"""
    __tablename__ = 'coupons'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(100), nullable=False, unique=True, index=True)
    subscription_tier = Column(String(50), nullable=False)  # pro, pro_plus
    description = Column(Text, nullable=True)
    max_uses = Column(Integer, nullable=True)  # None = unlimited
    used_count = Column(Integer, default=0, server_default='0')
    expires_at = Column(DateTime, nullable=True)  # None = never expires
    is_active = Column(Boolean, default=True, server_default='1')
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    created_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    
    # Relationships
    redemptions = relationship("CouponRedemption", back_populates="coupon", cascade="all, delete-orphan")
    
    __table_args__ = (
        CheckConstraint("subscription_tier IN ('pro', 'pro_plus')", name='check_coupon_tier'),
        Index('idx_coupons_code', 'code'),
        Index('idx_coupons_is_active', 'is_active'),
    )


class CouponRedemption(Base):
    """Coupon redemption model to track which users redeemed which coupons"""
    __tablename__ = 'coupon_redemptions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    coupon_id = Column(Integer, ForeignKey('coupons.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    redeemed_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    
    # Relationships
    coupon = relationship("Coupon", back_populates="redemptions")
    
    __table_args__ = (
        UniqueConstraint('coupon_id', 'user_id', name='unique_coupon_user'),
        Index('idx_coupon_redemptions_user_id', 'user_id'),
        Index('idx_coupon_redemptions_coupon_id', 'coupon_id'),
    )


class SystemSettings(Base):
    """System settings model for storing application-wide configuration"""
    __tablename__ = 'system_settings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(255), nullable=False, unique=True, index=True)
    value = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                       server_default=func.now(), server_onupdate=func.now())
    updated_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    
    __table_args__ = (
        Index('idx_system_settings_key', 'key'),
    )


class UserSettings(Base):
    """User-specific settings model for storing user preferences"""
    __tablename__ = 'user_settings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    selected_model = Column(String(255), nullable=True)  # User's selected model (if allowed)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                       server_default=func.now(), server_onupdate=func.now())
    
    __table_args__ = (
        Index('idx_user_settings_user_id', 'user_id'),
    )


class LLMModelPricing(Base):
    """Per (provider, model) pricing for LLM cost estimation (USD per 1M tokens)."""
    __tablename__ = 'llm_model_pricing'

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(64), nullable=False, index=True)
    model = Column(String(255), nullable=False, index=True)
    input_usd_per_million = Column(Numeric(20, 10), nullable=False, default=0)
    output_usd_per_million = Column(Numeric(20, 10), nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        server_default=func.now(), server_onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('provider', 'model', name='uq_llm_pricing_provider_model'),
        Index('idx_llm_pricing_provider_model', 'provider', 'model'),
    )


class LLMUsageEvent(Base):
    """Per-request LLM telemetry for cost and usage analytics."""
    __tablename__ = 'llm_usage_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True, server_default=func.now())

    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    user_role = Column(String(50), nullable=True)
    traffic_source = Column(String(32), nullable=False, default='production', server_default='production', index=True)
    workflow = Column(String(64), nullable=False, default='unknown', server_default='unknown', index=True)

    provider = Column(String(64), nullable=False, index=True)
    model = Column(String(255), nullable=False, index=True)

    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(24, 12), nullable=True)

    duration_ms = Column(Integer, nullable=False, default=0, server_default='0')
    success = Column(Boolean, nullable=False, default=True, server_default='1')
    error_class = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)

    conversation_id = Column(Integer, nullable=True, index=True)
    thread_id = Column(String(255), nullable=True, index=True)
    celery_task_name = Column(String(255), nullable=True, index=True)

    user = relationship("User", back_populates="llm_usage_events")

    __table_args__ = (
        Index('idx_llm_usage_created_at', 'created_at'),
        Index('idx_llm_usage_workflow_created', 'workflow', 'created_at'),
        Index('idx_llm_usage_user_created', 'user_id', 'created_at'),
        Index('idx_llm_usage_traffic_created', 'traffic_source', 'created_at'),
    )


class RouterDecisionEvent(Base):
    """
    Structured trace of one turn-intent routing decision (Phase 4 of the routing rework).

    One row per chat_node turn where the router freshly classified the turn (not on
    same-turn re-entries that reuse a cached verdict - see _classify_turn_intent's caller
    in rag_service.py). Deliberately a separate table from LLMUsageEvent: LLMUsageEvent is
    per-LLM-call (a turn can produce several rows - main completion, retries, lecture
    failsafe eval/regen, and the router's own structured-output call), while a routing
    decision is a per-turn concept. See PHASE4_DESIGN.md section 1 for the full rationale.
    """
    __tablename__ = 'router_decision_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True, server_default=func.now())

    # Same actor/context columns as LLMUsageEvent, for standalone queries without a join.
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    user_role = Column(String(50), nullable=True)
    traffic_source = Column(String(32), nullable=False, default='production', server_default='production', index=True)
    workflow = Column(String(64), nullable=False, default='unknown', server_default='unknown', index=True)
    conversation_id = Column(Integer, nullable=True, index=True)   # unconstrained, matches LLMUsageEvent.conversation_id
    thread_id = Column(String(255), nullable=True, index=True)

    # Link to the router's own structured-output LLM call, if/when one is correlated back to
    # its LLMUsageEvent row. Nullable: not populated in the initial implementation (persisting
    # the created LLMUsageEvent's id would require changing llm_gateway.py's shared write path
    # used by every LLM call in the app; deferred as a documented follow-up rather than widening
    # that hot path's blast radius here). Kept for future use / manual correlation by timestamp.
    router_llm_usage_event_id = Column(Integer, ForeignKey('llm_usage_events.id', ondelete='SET NULL'), nullable=True, index=True)

    # --- RouterOutput fields ---
    intent = Column(String(64), nullable=True, index=True)
    requested_brevity = Column(Boolean, nullable=True)
    meta_conversation_scope = Column(String(32), nullable=True)
    meta_conversation_n = Column(Integer, nullable=True)
    reasoning = Column(Text, nullable=True)

    # --- Failure / fallback tracking ---
    # True whenever _router_fallback_from_regex fired (router disabled, invalid verdict type,
    # or the structured-output call raised) instead of a real router LLM classification.
    router_used_fallback = Column(Boolean, nullable=False, default=False, server_default='0', index=True)
    fallback_reason = Column(String(255), nullable=True)

    # --- Downstream branch/suppression flags actually taken this turn ---
    prefetch_branch = Column(String(64), nullable=True)
    meta_conversation_active = Column(Boolean, nullable=False, default=False, server_default='0')
    own_answer_followup_active = Column(Boolean, nullable=False, default=False, server_default='0')
    tool_rounds_used = Column(Integer, nullable=True)
    tool_round_limit_reached = Column(Boolean, nullable=False, default=False, server_default='0')

    # --- Turn outcome ---
    outcome = Column(String(16), nullable=False, default='success', server_default='success', index=True)
    error_class = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    user = relationship("User")

    __table_args__ = (
        Index('idx_router_decision_created_at', 'created_at'),
        Index('idx_router_decision_intent_created', 'intent', 'created_at'),
        Index('idx_router_decision_fallback_created', 'router_used_fallback', 'created_at'),
        Index('idx_router_decision_user_created', 'user_id', 'created_at'),
    )

