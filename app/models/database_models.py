"""SQLAlchemy database models for the application"""
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, Date, ForeignKey, 
    CheckConstraint, UniqueConstraint, Index, func
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
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())
    
    # Relationships
    user = relationship("User", back_populates="rag_threads")
    rag_prompts = relationship("RAGPrompt", back_populates="thread", cascade="all, delete-orphan")
    user_documents = relationship("UserDocument", back_populates="rag_thread")
    rag_chunks = relationship("RAGChunk", back_populates="thread", cascade="all, delete-orphan")
    
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

