"""
Phase 1 new models: AI personalities, admin audit log, platform subjects,
content library, exam syllabus tree, parent-student linking, enrollment
waitlist, notification inbox, and terms acceptance.

All models share the same Base as database_models.py to participate in
the same metadata / migration target.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.models.database_models import Base


# ---------------------------------------------------------------------------
# AI Teaching Personalities
# ---------------------------------------------------------------------------

class AIPersonality(Base):
    """
    Teaching personality that can be assigned to a student.
    The system_prompt_modifier is prepended to the AI system prompt.
    Exactly one row should have is_default=True (enforced by application).
    """

    __tablename__ = "ai_personalities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(64), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    system_prompt_modifier = Column(Text, nullable=False)
    is_default = Column(Boolean, nullable=False, default=False, server_default="0")
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        Index("idx_ai_personalities_slug", "slug"),
        Index("idx_ai_personalities_active", "is_active"),
    )


# ---------------------------------------------------------------------------
# Admin Creation Audit Log
# ---------------------------------------------------------------------------

class AdminCreationLog(Base):
    """
    Immutable audit log written every time a higher-role admin creates/invites
    a lower-role user. Records who created whom, what role was granted, and
    the school scope (if any).
    """

    __tablename__ = "admin_creation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    role_assigned = Column(String(50), nullable=False)
    scope_school_id = Column(Integer, ForeignKey("schools.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        Index("idx_admin_creation_logs_creator", "creator_id"),
        Index("idx_admin_creation_logs_created", "created_user_id"),
    )


# ---------------------------------------------------------------------------
# Platform-level Subject Catalog
# ---------------------------------------------------------------------------

class PlatformSubject(Base):
    """
    Platform-wide subject catalog (distinct from school-scoped SchoolSubject).
    Used by the content library and syllabus engine.
    """

    __tablename__ = "platform_subjects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    short_code = Column(String(64), nullable=False, unique=True)
    grade_bands = Column(String(255), nullable=True)  # comma-separated, e.g. "6,7,8,9"
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    content_books = relationship("ContentBook", back_populates="platform_subject")
    syllabus_chapters = relationship("SyllabusChapter", back_populates="platform_subject")

    __table_args__ = (
        Index("idx_platform_subjects_code", "short_code"),
        Index("idx_platform_subjects_active", "is_active"),
    )


# ---------------------------------------------------------------------------
# Platform Content Library
# ---------------------------------------------------------------------------

class ContentBook(Base):
    """
    Reference book uploaded by admins to the platform content library.
    Can be scoped to a platform subject, a school subject, or both.
    """

    __tablename__ = "content_books"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform_subject_id = Column(
        Integer, ForeignKey("platform_subjects.id", ondelete="RESTRICT"), nullable=True
    )
    school_subject_id = Column(
        Integer, ForeignKey("school_subjects.id", ondelete="SET NULL"), nullable=True
    )
    grade = Column(String(64), nullable=False)
    title = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=False)
    file_size = Column(Integer, nullable=True)
    mime_type = Column(String(100), nullable=True)
    metadata_json = Column(Text, nullable=True)  # free JSON: publisher, edition, etc.
    uploaded_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
    )

    platform_subject = relationship("PlatformSubject", back_populates="content_books")

    __table_args__ = (
        Index("idx_content_books_subject", "platform_subject_id"),
        Index("idx_content_books_grade", "grade"),
        Index("idx_content_books_active", "is_active"),
    )


# ---------------------------------------------------------------------------
# Exam Syllabus Engine
# ---------------------------------------------------------------------------

class ExamType(Base):
    """Top-level exam type (e.g. Board Exam, Mid-Term, Mock Exam)."""

    __tablename__ = "exam_types"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    code = Column(String(64), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    chapters = relationship("SyllabusChapter", back_populates="exam_type", cascade="all, delete-orphan")


class SyllabusChapter(Base):
    """A chapter within a specific exam type + subject + grade."""

    __tablename__ = "syllabus_chapters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exam_type_id = Column(Integer, ForeignKey("exam_types.id", ondelete="CASCADE"), nullable=False)
    platform_subject_id = Column(Integer, ForeignKey("platform_subjects.id", ondelete="CASCADE"), nullable=False)
    grade = Column(String(64), nullable=False)
    title = Column(String(500), nullable=False)
    chapter_number = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    exam_type = relationship("ExamType", back_populates="chapters")
    platform_subject = relationship("PlatformSubject", back_populates="syllabus_chapters")
    topics = relationship("SyllabusTopic", back_populates="chapter", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_syllabus_chapters_exam", "exam_type_id"),
        Index("idx_syllabus_chapters_subject", "platform_subject_id"),
        Index("idx_syllabus_chapters_grade", "grade"),
        UniqueConstraint(
            "exam_type_id", "platform_subject_id", "grade", "chapter_number",
            name="uq_chapter_identity",
        ),
    )


class SyllabusTopic(Base):
    """A topic within a syllabus chapter."""

    __tablename__ = "syllabus_topics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chapter_id = Column(Integer, ForeignKey("syllabus_chapters.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(500), nullable=False)
    order_index = Column(Integer, nullable=False, default=0, server_default="0")
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    chapter = relationship("SyllabusChapter", back_populates="topics")
    sub_topics = relationship("SyllabusSubTopic", back_populates="topic", cascade="all, delete-orphan")

    __table_args__ = (Index("idx_syllabus_topics_chapter", "chapter_id"),)


class SyllabusSubTopic(Base):
    """A sub-topic within a syllabus topic."""

    __tablename__ = "syllabus_sub_topics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic_id = Column(Integer, ForeignKey("syllabus_topics.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(500), nullable=False)
    order_index = Column(Integer, nullable=False, default=0, server_default="0")
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    topic = relationship("SyllabusTopic", back_populates="sub_topics")

    __table_args__ = (Index("idx_syllabus_sub_topics_topic", "topic_id"),)


# ---------------------------------------------------------------------------
# Parent-Student Linking
# ---------------------------------------------------------------------------

class ParentStudentLink(Base):
    """
    A parent requests to link to a student account. The student must approve.
    Parent access is view-only (enforced in the service/route layer).
    """

    __tablename__ = "parent_student_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    requested_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    parent = relationship("User", foreign_keys=[parent_id], back_populates="parent_links")
    student = relationship("User", foreign_keys=[student_id], back_populates="child_links")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="check_parent_link_status",
        ),
        UniqueConstraint("parent_id", "student_id", name="uq_parent_student_link"),
        Index("idx_parent_student_links_parent", "parent_id"),
        Index("idx_parent_student_links_student", "student_id"),
        Index("idx_parent_student_links_status", "status"),
    )


# ---------------------------------------------------------------------------
# Enrollment Waitlist
# ---------------------------------------------------------------------------

class EnrollmentWaitlist(Base):
    """
    When a class section is at max_capacity, new enrollment requests land here.
    position is 1-based and maintained by the service layer.
    On dropout, the student at position 1 is promoted and positions are reordered.
    """

    __tablename__ = "enrollment_waitlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    class_section_id = Column(
        Integer, ForeignKey("class_sections.id", ondelete="CASCADE"), nullable=False
    )
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    class_section = relationship("ClassSection", back_populates="waitlist_entries")

    __table_args__ = (
        UniqueConstraint("class_section_id", "student_user_id", name="uq_waitlist_student_section"),
        Index("idx_waitlist_section", "class_section_id"),
        Index("idx_waitlist_student", "student_user_id"),
    )


# ---------------------------------------------------------------------------
# Notification Inbox
# ---------------------------------------------------------------------------

class Notification(Base):
    """
    Universal notification inbox for all roles.
    Every platform event (suspension, waitlist promotion, parent link, etc.)
    creates one or more Notification rows for the relevant recipient(s).
    """

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recipient_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    type = Column(String(64), nullable=False, default="info", server_default="info")
    is_read = Column(Boolean, nullable=False, default=False, server_default="0")
    action_link = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    recipient = relationship("User", foreign_keys=[recipient_id], back_populates="notifications")

    __table_args__ = (
        Index("idx_notifications_recipient", "recipient_id"),
        Index("idx_notifications_is_read", "is_read"),
        Index("idx_notifications_created", "created_at"),
    )


# ---------------------------------------------------------------------------
# Terms of Service Acceptance
# ---------------------------------------------------------------------------

class TermsAcceptance(Base):
    """
    Immutable record of a user accepting the platform Terms of Service.
    One row per (user, terms_version). The user.terms_accepted_version
    field is the denormalized fast-check equivalent.
    """

    __tablename__ = "terms_acceptances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    terms_version = Column(String(32), nullable=False)
    accepted_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    ip_address = Column(String(64), nullable=True)

    user = relationship("User", back_populates="terms_acceptances")

    __table_args__ = (
        UniqueConstraint("user_id", "terms_version", name="uq_terms_acceptance"),
        Index("idx_terms_acceptances_user", "user_id"),
    )
