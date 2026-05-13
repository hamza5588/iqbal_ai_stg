"""
Phase 4 — AI intelligence, practice pipeline, predictions, groups, VA, pedagogy.
Shares Base with database_models / phase3.
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

QUEUE_SOURCES = (
    "gap_recovery",
    "urgent_weak_topic",
    "wrong_repeat",
    "spaced_repetition",
    "daily_practice",
    "micro_revision",
)

# Priority order for sorting (lower = higher priority)
QUEUE_SOURCE_PRIORITY = {
    "gap_recovery": 1,
    "urgent_weak_topic": 2,
    "wrong_repeat": 3,
    "spaced_repetition": 4,
    "daily_practice": 5,
    "micro_revision": 6,
}


class StudentQuestionQueueItem(Base):
    """Unified delivery queue for practice questions."""

    __tablename__ = "student_question_queue_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    question_bank_item_id = Column(
        Integer, ForeignKey("question_bank_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source = Column(String(32), nullable=False)
    due_at = Column(DateTime, nullable=True, index=True)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "source IN ('gap_recovery','urgent_weak_topic','wrong_repeat','spaced_repetition',"
            "'daily_practice','micro_revision')",
            name="check_queue_source",
        ),
        CheckConstraint("status IN ('pending','dispatched','completed','skipped')", name="check_queue_status"),
        Index("idx_queue_student_status_due", "student_user_id", "status", "due_at"),
    )


class QuestionPracticeAttempt(Base):
    """Per-question attempt: timer, confidence, guess flag, error classification."""

    __tablename__ = "question_practice_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    question_bank_item_id = Column(
        Integer, ForeignKey("question_bank_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    queue_item_id = Column(
        Integer, ForeignKey("student_question_queue_items.id", ondelete="SET NULL"), nullable=True
    )
    started_at = Column(DateTime, nullable=False)
    answered_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    confidence_before_result = Column(Integer, nullable=True)  # 1–5
    response_payload_json = Column(Text, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    is_guess = Column(Boolean, nullable=False, default=False, server_default="0")
    guess_signals_json = Column(Text, nullable=True)
    exclude_from_pass_probability = Column(Boolean, nullable=False, default=False, server_default="0")
    error_type = Column(String(32), nullable=True)
    error_explanation = Column(Text, nullable=True)
    similar_followup_item_id = Column(
        Integer, ForeignKey("question_bank_items.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "confidence_before_result IS NULL OR (confidence_before_result >= 1 AND confidence_before_result <= 5)",
            name="check_confidence_range",
        ),
        CheckConstraint(
            "error_type IS NULL OR error_type IN ('careless','conceptual','misunderstanding')",
            name="check_error_type",
        ),
        Index("idx_attempts_student_created", "student_user_id", "created_at"),
    )


class StudentMistakeCounter(Base):
    """Aggregated wrong answers by topic/concept/error type."""

    __tablename__ = "student_mistake_counters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    syllabus_topic_id = Column(Integer, ForeignKey("syllabus_topics.id", ondelete="SET NULL"), nullable=True)
    concept_key = Column(String(255), nullable=False, default="", server_default="")
    error_type = Column(String(32), nullable=True)
    mistake_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_occurred_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "student_user_id",
            "syllabus_topic_id",
            "concept_key",
            "error_type",
            name="uq_mistake_student_topic_concept_err",
        ),
        Index("idx_mistake_student_count", "student_user_id", "mistake_count"),
    )


class StudentConceptSchedule(Base):
    """Spaced repetition / forgetting curve per concept."""

    __tablename__ = "student_concept_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    concept_key = Column(String(255), nullable=False)
    syllabus_topic_id = Column(Integer, ForeignKey("syllabus_topics.id", ondelete="SET NULL"), nullable=True)
    ease_factor = Column(Numeric(6, 3), nullable=False, default=2.5, server_default="2.5")
    interval_days = Column(Integer, nullable=False, default=1, server_default="1")
    repetitions = Column(Integer, nullable=False, default=0, server_default="0")
    last_reviewed_at = Column(DateTime, nullable=True)
    next_review_at = Column(DateTime, nullable=True, index=True)
    strength_estimate = Column(Numeric(5, 4), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("student_user_id", "concept_key", name="uq_concept_schedule_student_key"),
        Index("idx_concept_schedule_next", "student_user_id", "next_review_at"),
    )


class StudentCognitiveSnapshot(Base):
    """Explainable cognitive DNA + radar-ready payloads."""

    __tablename__ = "student_cognitive_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    dna_json = Column(Text, nullable=False)
    radar_student_json = Column(Text, nullable=True)
    radar_raw_json = Column(Text, nullable=True)


class StudentIntelligenceSnapshot(Base):
    """Nightly (or on-demand) pass probability, exam confidence, marks, causal, recs."""

    __tablename__ = "student_intelligence_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_target_id = Column(Integer, ForeignKey("student_exam_targets.id", ondelete="SET NULL"), nullable=True)
    pass_probability = Column(Numeric(5, 4), nullable=True)
    exam_confidence = Column(Numeric(5, 4), nullable=True)
    marks_low = Column(Numeric(6, 2), nullable=True)
    marks_high = Column(Numeric(6, 2), nullable=True)
    inputs_json = Column(Text, nullable=False)
    causal_topics_json = Column(Text, nullable=True)
    recommendations_json = Column(Text, nullable=True)
    two_future_json = Column(Text, nullable=True)
    days_to_exam = Column(Integer, nullable=True)
    risk_urgency = Column(String(32), nullable=True)
    computed_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)

    __table_args__ = (Index("idx_intel_student_computed", "student_user_id", "computed_at"),)


class ExamConfidenceDaily(Base):
    """Daily exam confidence series for trends."""

    __tablename__ = "exam_confidence_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_target_id = Column(Integer, ForeignKey("student_exam_targets.id", ondelete="SET NULL"), nullable=True)
    day = Column(Date, nullable=False)
    score = Column(Numeric(5, 4), nullable=False)
    factors_json = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("student_user_id", "exam_target_id", "day", name="uq_exam_conf_day"),
        Index("idx_exam_conf_student_day", "student_user_id", "day"),
    )


class GapAnalysisConsent(Base):
    """Student consent for deeper gap / root-cause analysis."""

    __tablename__ = "gap_analysis_consents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    consented_at = Column(DateTime, nullable=True)
    result_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected','completed')", name="check_gap_consent_status"),
    )


class RecoveryBundleSession(Base):
    """Full recovery flow: example → mini lecture → practice → badge."""

    __tablename__ = "recovery_bundle_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    syllabus_topic_id = Column(Integer, ForeignKey("syllabus_topics.id", ondelete="SET NULL"), nullable=True)
    current_step = Column(String(32), nullable=False, default="example", server_default="example")
    practice_remaining = Column(Integer, nullable=False, default=5, server_default="5")
    skip_allowed = Column(Boolean, nullable=False, default=False, server_default="0")
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "current_step IN ('example','mini_lecture','practice','badge','completed')",
            name="check_recovery_step",
        ),
    )


class MicroRevisionSession(Base):
    """Micro-revision with style rotation and optional 30s recall."""

    __tablename__ = "micro_revision_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    syllabus_topic_id = Column(Integer, ForeignKey("syllabus_topics.id", ondelete="SET NULL"), nullable=True)
    style = Column(String(32), nullable=False)
    recall_deadline_at = Column(DateTime, nullable=True)
    recall_response_text = Column(Text, nullable=True)
    recall_feedback_json = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "style IN ('real_world_example','story','exam_question','analogy','visual_explanation')",
            name="check_micro_style",
        ),
        CheckConstraint("status IN ('active','completed','expired')", name="check_micro_status"),
    )


class ScheduledNotification(Base):
    """Precise fire-at reminders (Celery eta)."""

    __tablename__ = "scheduled_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recipient_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    fire_at_utc = Column(DateTime, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    message = Column(Text, nullable=False)
    notif_type = Column(String(64), nullable=False, default="reminder", server_default="reminder")
    action_link = Column(String(1000), nullable=True)
    payload_json = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    celery_task_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending','sent','failed','cancelled')", name="check_sched_notif_status"),
        Index("idx_sched_notif_pending_fire", "status", "fire_at_utc"),
    )


class StudyGroup(Base):
    """Student-created (or teacher-accepted AI) study group."""

    __tablename__ = "study_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(500), nullable=False)
    purpose = Column(String(255), nullable=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=False)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (CheckConstraint("status IN ('active','archived')", name="check_study_group_status"),)


class StudyGroupMember(Base):
    __tablename__ = "study_group_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("study_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(32), nullable=False, default="member", server_default="member")
    joined_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_member"),
        CheckConstraint("role IN ('owner','member')", name="check_group_member_role"),
    )


class StudyGroupInvite(Base):
    __tablename__ = "study_group_invites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("study_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    max_uses = Column(Integer, nullable=False, default=50, server_default="50")
    use_count = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())


class StudyGroupNotes(Base):
    """Collaborative notes (version for optimistic concurrency)."""

    __tablename__ = "study_group_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("study_groups.id", ondelete="CASCADE"), nullable=False, unique=True)
    body_text = Column(Text, nullable=False, default="", server_default="")
    version = Column(Integer, nullable=False, default=1, server_default="1")
    updated_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())


class StudyGroupAIThread(Base):
    __tablename__ = "study_group_ai_threads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("study_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    scope = Column(String(32), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint("scope IN ('shared','private')", name="check_ai_thread_scope"),
        # At most one shared thread per group and one private thread per member — enforced in service
        # (SQL UNIQUE with NULL user_id is not portable for "one shared row").
        Index("idx_group_ai_thread_lookup", "group_id", "scope", "user_id"),
    )


class StudyGroupAIMessage(Base):
    __tablename__ = "study_group_ai_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(Integer, ForeignKey("study_group_ai_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    role = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    sources_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (CheckConstraint("role IN ('user','assistant')", name="check_ai_msg_role"),)


class TeacherGroupSuggestion(Base):
    """AI-suggested groups for a teacher to accept."""

    __tablename__ = "teacher_group_suggestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    teacher_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    class_section_id = Column(Integer, ForeignKey("class_sections.id", ondelete="SET NULL"), nullable=True)
    payload_json = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    resolved_study_group_id = Column(Integer, ForeignKey("study_groups.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending','accepted','dismissed')", name="check_tgs_status"),
    )


class Phase4ChatConversation(Base):
    __tablename__ = "phase4_chat_conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_hint = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())


class Phase4ChatMessage(Base):
    __tablename__ = "phase4_chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(
        Integer, ForeignKey("phase4_chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    sources_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (CheckConstraint("role IN ('user','assistant')", name="check_p4chat_role"),)


class VirtualAssistantCard(Base):
    __tablename__ = "virtual_assistant_cards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    card_type = Column(String(64), nullable=False)
    title = Column(String(500), nullable=False)
    body_json = Column(Text, nullable=False)
    action_cta = Column(String(500), nullable=True)
    due_at = Column(DateTime, nullable=True)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    snooze_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','dismissed','completed','snoozed')", name="check_va_card_status"
        ),
        Index("idx_va_user_status", "user_id", "status"),
    )


class AIPedagogyTemplate(Base):
    """Live explanation template per syllabus topic (optional)."""

    __tablename__ = "ai_pedagogy_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    syllabus_topic_id = Column(Integer, ForeignKey("syllabus_topics.id", ondelete="CASCADE"), nullable=False)
    template_key = Column(String(64), nullable=False, default="default", server_default="default")
    body = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("syllabus_topic_id", "template_key", name="uq_pedagogy_topic_key"),
    )


class AIPedagogyTemplateProposal(Base):
    __tablename__ = "ai_pedagogy_template_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    syllabus_topic_id = Column(Integer, ForeignKey("syllabus_topics.id", ondelete="CASCADE"), nullable=False)
    proposed_body = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    critique_json = Column(Text, nullable=True)
    reviewer_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected')", name="check_pedagogy_proposal_status"),
    )


class Phase4AdminAuditLog(Base):
    """Audit trail for pedagogy approval and sensitive Phase 4 admin actions."""

    __tablename__ = "phase4_admin_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(128), nullable=False)
    entity_type = Column(String(64), nullable=True)
    entity_id = Column(Integer, nullable=True)
    detail_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (Index("idx_p4_audit_actor", "actor_user_id", "created_at"),)


class ParentRiskAlertState(Base):
    """Track parent alert cadence / escalation for pass probability drops."""

    __tablename__ = "parent_risk_alert_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    last_alert_at = Column(DateTime, nullable=True)
    last_pass_probability = Column(Numeric(5, 4), nullable=True)
    cadence = Column(String(32), nullable=False, default="weekly", server_default="weekly")
    meta_json = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("student_user_id", "parent_user_id", name="uq_parent_risk_student_parent"),
    )
