"""
Learning delivery tables aligned with product spec:
- Publish one lesson to multiple class sections (reuse across sections).
- Quiz sessions with device vs screen delivery; submissions and scores.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.models.database_models import Base


class LectureClassSection(Base):
    """Many-to-many: same lecture visible to multiple sections (same material, different cohorts)."""

    __tablename__ = "lecture_class_sections"

    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    class_section_id = Column(
        Integer, ForeignKey("class_sections.id", ondelete="CASCADE"), primary_key=True
    )
    published_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        Index("idx_lecture_class_sections_section", "class_section_id"),
    )


class QuizSession(Base):
    """
    Teacher-triggered MCQ block for a lesson + cohort.
    questions_json: list of { "id": str, "prompt": str, "choices": [str], "correct_index": int }
    """

    __tablename__ = "quiz_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    class_section_id = Column(
        Integer, ForeignKey("class_sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    teacher_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    delivery_mode = Column(String(16), nullable=False)
    questions_json = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    closed_at = Column(DateTime, nullable=True)

    submissions = relationship(
        "QuizSubmission", back_populates="quiz_session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("delivery_mode IN ('device', 'screen')", name="check_quiz_delivery_mode"),
        CheckConstraint("status IN ('draft', 'active', 'closed')", name="check_quiz_session_status"),
        Index("idx_quiz_sessions_class", "class_section_id"),
    )


class QuizSubmission(Base):
    """One student's answers for a quiz session (device or after manual entry for screen mode)."""

    __tablename__ = "quiz_submissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quiz_session_id = Column(
        Integer, ForeignKey("quiz_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    answers_json = Column(Text, nullable=False)
    score = Column(Numeric(6, 3), nullable=True)
    max_score = Column(Numeric(6, 3), nullable=True)
    submitted_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    quiz_session = relationship("QuizSession", back_populates="submissions")

    __table_args__ = (
        UniqueConstraint("quiz_session_id", "student_user_id", name="uq_quiz_submission_per_student"),
        Index("idx_quiz_submissions_student", "student_user_id"),
    )
