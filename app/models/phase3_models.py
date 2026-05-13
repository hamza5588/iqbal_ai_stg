"""
Phase 3 — Student learning experience: question bank, learning events,
captured questions, study artifacts, and teacher review linkage.

Uses the same Base as database_models / phase1_models.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
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

from app.models.database_models import Base

BLOOM_LEVELS = (
    "remember",
    "understand",
    "apply",
    "analyse",
    "evaluate",
    "create",
)


class QuestionBankItem(Base):
    """Foundational reusable question entry (Feature 34)."""

    __tablename__ = "question_bank_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    syllabus_topic_id = Column(
        Integer, ForeignKey("syllabus_topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    stem = Column(Text, nullable=False)
    difficulty = Column(Integer, nullable=False, default=3, server_default="3")
    bloom_level = Column(String(32), nullable=False, default="understand", server_default="understand")
    tags_json = Column(Text, nullable=True)
    source = Column(String(255), nullable=True)
    explanation = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("difficulty >= 1 AND difficulty <= 5", name="check_qb_difficulty"),
        CheckConstraint(
            "bloom_level IN ('remember','understand','apply','analyse','evaluate','create')",
            name="check_qb_bloom",
        ),
        Index("idx_qb_active", "is_active"),
    )


class LearningEvent(Base):
    """Append-only interaction log (Feature 60). Fan-out via Celery optional."""

    __tablename__ = "learning_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False, index=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True, index=True)
    session_key = Column(String(64), nullable=True, index=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), index=True)

    __table_args__ = (Index("idx_learning_events_type_created", "event_type", "created_at"),)


class StudentLearningQuestion(Base):
    """Captured student questions with classification fields."""

    __tablename__ = "student_learning_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=True, index=True)
    mode = Column(String(32), nullable=False)
    question_text = Column(Text, nullable=False)
    source_context_json = Column(Text, nullable=True)
    lesson_chat_history_id = Column(Integer, nullable=True)
    understanding_label = Column(String(32), nullable=True)
    understanding_confidence = Column(Numeric(5, 4), nullable=True)
    understanding_meta_json = Column(Text, nullable=True)
    is_critical = Column(Boolean, nullable=False, default=False, server_default="0")
    canonical_fingerprint = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), index=True)

    __table_args__ = (
        CheckConstraint("mode IN ('lecture','self_study')", name="check_slq_mode"),
        CheckConstraint(
            "understanding_label IS NULL OR understanding_label IN "
            "('misconception','knowledge_gap','clarification')",
            name="check_slq_understanding",
        ),
        Index("idx_slq_lesson_fp", "lesson_id", "canonical_fingerprint"),
    )


class LectureTeacherReview(Base):
    """Teacher review bundle linked to lecture."""

    __tablename__ = "lecture_teacher_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ai_summary = Column(Text, nullable=True)
    reflection_prompt = Column(Text, nullable=True)
    reflection_response = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now()
    )

    __table_args__ = (Index("idx_ltr_lesson_teacher", "lesson_id", "teacher_user_id"),)


class MiniLectureTarget(Base):
    """Mini-lecture visibility restricted to selected students."""

    __tablename__ = "mini_lecture_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mini_lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_review_id = Column(Integer, ForeignKey("lecture_teacher_reviews.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("mini_lesson_id", "student_user_id", name="uq_mini_target_student"),
    )


class StudentLectureRating(Base):
    """Post-engagement lecture rating."""

    __tablename__ = "student_lecture_ratings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False)
    stars = Column(Integer, nullable=False)
    comment = Column(Text, nullable=True)
    engagement_seconds = Column(Integer, nullable=True)
    threshold_seconds_required = Column(Integer, nullable=False, default=120, server_default="120")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint("stars >= 1 AND stars <= 5", name="check_rating_stars"),
        UniqueConstraint("student_user_id", "lesson_id", name="uq_rating_student_lesson"),
    )


class StudentDiagnosticProfile(Base):
    """Optional diagnostic baseline."""

    __tablename__ = "student_diagnostic_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    status = Column(String(32), nullable=False, default="skipped", server_default="skipped")
    baseline_json = Column(Text, nullable=True)
    skipped = Column(Boolean, nullable=False, default=True, server_default="1")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())


class StudentExamTarget(Base):
    """Exam countdown driver."""

    __tablename__ = "student_exam_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_type_id = Column(Integer, ForeignKey("exam_types.id", ondelete="SET NULL"), nullable=True)
    exam_date = Column(Date, nullable=False)
    label = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (Index("idx_exam_target_student_date", "student_user_id", "exam_date"),)


class StudentContentHighlight(Base):
    """Persisted highlights."""

    __tablename__ = "student_content_highlights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=True, index=True)
    mode = Column(String(32), nullable=False)
    anchor_json = Column(Text, nullable=False)
    excerpt = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (CheckConstraint("mode IN ('lecture','self_study')", name="check_highlight_mode"),)


class StudentFlashcard(Base):
    """Flashcards + SRS payload."""

    __tablename__ = "student_flashcards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    front = Column(Text, nullable=False)
    back = Column(Text, nullable=False)
    highlight_id = Column(
        Integer, ForeignKey("student_content_highlights.id", ondelete="SET NULL"), nullable=True
    )
    srs_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now()
    )


class StudentLectureProgress(Base):
    """Progress split by lecture vs self-study mode."""

    __tablename__ = "student_lecture_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False)
    mode = Column(String(32), nullable=False)
    position_json = Column(Text, nullable=True)
    percent_complete = Column(Numeric(5, 2), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint("mode IN ('lecture','self_study')", name="check_progress_mode"),
        UniqueConstraint("student_user_id", "lesson_id", "mode", name="uq_progress_triple"),
        Index("idx_slp_student", "student_user_id"),
    )


class StudentStudyPlan(Base):
    """Structured study plan JSON."""

    __tablename__ = "student_study_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    plan_json = Column(Text, nullable=False)
    horizon_days = Column(Integer, nullable=True)
    exam_target_id = Column(Integer, ForeignKey("student_exam_targets.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now()
    )


class StudentLearningPreferences(Base):
    """Privacy + reminders + streak."""

    __tablename__ = "student_learning_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    allow_teacher_view_self_study = Column(Boolean, nullable=False, default=True, server_default="1")
    reminder_channels_json = Column(Text, nullable=True)
    reminder_state_json = Column(Text, nullable=True)
    daily_goal_minutes = Column(Integer, nullable=True)
    streak_days = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())


class StudentOwnedUpload(Base):
    """Prep vs content uploads."""

    __tablename__ = "student_owned_uploads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(32), nullable=False)
    storage_path = Column(String(1000), nullable=False)
    mime_type = Column(String(128), nullable=True)
    original_name = Column(String(500), nullable=True)
    ocr_status = Column(String(32), nullable=False, default="pending", server_default="pending")
    ocr_extracted_text = Column(Text, nullable=True)
    ai_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint("category IN ('prep_book','content_book')", name="check_upload_category"),
    )


class PrepBookTopicAnalysis(Base):
    """Cached topic extraction."""

    __tablename__ = "prep_book_topic_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    upload_id = Column(Integer, ForeignKey("student_owned_uploads.id", ondelete="CASCADE"), nullable=False)
    topics_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())


class AITeachingAdaptation(Base):
    """Explainable adaptation log."""

    __tablename__ = "ai_teaching_adaptations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True, index=True)
    session_key = Column(String(64), nullable=True, index=True)
    adaptation_type = Column(String(64), nullable=False)
    reason = Column(Text, nullable=False)
    meta_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())


class StudentPlanAdherence(Base):
    """Daily adherence."""

    __tablename__ = "student_plan_adherence"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    day = Column(Date, nullable=False)
    planned_minutes = Column(Integer, nullable=True)
    actual_minutes = Column(Integer, nullable=True)
    missed = Column(Boolean, nullable=False, default=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("student_user_id", "day", name="uq_adherence_student_day"),
        Index("idx_adherence_student", "student_user_id"),
    )


class SyllabusRealWorldSnippet(Base):
    """Cached career/real-world content per syllabus topic."""

    __tablename__ = "syllabus_realworld_snippets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    syllabus_topic_id = Column(
        Integer, ForeignKey("syllabus_topics.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    payload_json = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())


class ClassPositiveBenchmark(Base):
    """Anonymised positive aggregates."""

    __tablename__ = "class_positive_benchmarks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    class_section_id = Column(Integer, ForeignKey("class_sections.id", ondelete="CASCADE"), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    metrics_json = Column(Text, nullable=False)

    __table_args__ = (
        Index("idx_cpb_section_period", "class_section_id", "period_start", "period_end"),
    )


class UserCalendarConnection(Base):
    """Encrypted OAuth / CalDAV credentials for calendar sync (Google, Apple)."""

    __tablename__ = "user_calendar_connections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(32), nullable=False)
    encrypted_payload = Column(Text, nullable=False)
    account_hint = Column(String(255), nullable=True)
    sync_meta_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_calendar_user_provider"),
        Index("idx_calendar_user", "user_id"),
    )


class GroupStudySlot(Base):
    """Teacher-proposed group study session."""

    __tablename__ = "group_study_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    teacher_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(500), nullable=False)
    starts_at = Column(DateTime, nullable=False)
    ends_at = Column(DateTime, nullable=False)
    max_students = Column(Integer, nullable=False, default=8, server_default="8")
    notes = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="scheduled", server_default="scheduled")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('scheduled','cancelled','completed')", name="check_group_slot_status"),
        Index("idx_group_slots_teacher_start", "teacher_user_id", "starts_at"),
    )


class GroupStudyRsvp(Base):
    """Student signup for a group study slot."""

    __tablename__ = "group_study_rsvps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slot_id = Column(Integer, ForeignKey("group_study_slots.id", ondelete="CASCADE"), nullable=False, index=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="confirmed", server_default="confirmed")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("slot_id", "student_user_id", name="uq_group_rsvp_slot_student"),
        CheckConstraint("status IN ('confirmed','cancelled')", name="check_group_rsvp_status"),
    )
