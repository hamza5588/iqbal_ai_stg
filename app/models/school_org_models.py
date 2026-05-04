"""
School participant model layer: districts, schools, principal/coordinators,
subjects, class sections (grade + section + subject + teacher), coordinator
roster rows, and student enrollments.

Roll numbers are unique per class_section (one roster line). Student
self-registration links a users row to roster_entries.linked_student_user_id
and creates class_enrollments (implemented in a later step / service layer).

See product spec Section 7–8 (roster prerequisite, enrollment).
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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.models.database_models import Base


class District(Base):
    """Optional district / region for multi-school hierarchy."""

    __tablename__ = "districts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    code = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    schools = relationship("School", back_populates="district")


class School(Base):
    """A physical or logical school. Principal is the accountable party."""

    __tablename__ = "schools"

    id = Column(Integer, primary_key=True, autoincrement=True)
    district_id = Column(Integer, ForeignKey("districts.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    # Platform-wide short code (avoids ambiguous UNIQUE with nullable district_id)
    code = Column(String(64), nullable=False, unique=True, index=True)
    principal_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    district = relationship("District", back_populates="schools")
    coordinators = relationship("SchoolCoordinator", back_populates="school", cascade="all, delete-orphan")
    subjects = relationship("SchoolSubject", back_populates="school", cascade="all, delete-orphan")
    teacher_links = relationship("TeacherSchoolAffiliation", back_populates="school", cascade="all, delete-orphan")
    class_sections = relationship("ClassSection", back_populates="school", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="check_school_status"),
        Index("idx_schools_principal", "principal_user_id"),
    )


class SchoolCoordinator(Base):
    """Coordinators under the principal: HR/scheduling/quality for teachers."""

    __tablename__ = "school_coordinators"

    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    school = relationship("School", back_populates="coordinators")

    __table_args__ = (Index("idx_school_coordinators_user", "user_id"),)


class SchoolSubject(Base):
    """Subject catalogue scoped to a school (or templates if extended later)."""

    __tablename__ = "school_subjects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    short_code = Column(String(64), nullable=False)
    grade_band = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    school = relationship("School", back_populates="subjects")
    class_sections = relationship("ClassSection", back_populates="subject")

    __table_args__ = (
        UniqueConstraint("school_id", "short_code", name="uq_subject_code_per_school"),
        Index("idx_school_subjects_school", "school_id"),
    )


class TeacherSchoolAffiliation(Base):
    """Teacher belongs to a school (HR roster) before/parallel to class assignments."""

    __tablename__ = "teacher_school_affiliations"

    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), primary_key=True)
    teacher_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    school = relationship("School", back_populates="teacher_links")

    __table_args__ = (Index("idx_teacher_school_teacher", "teacher_user_id"),)


class ClassSection(Base):
    """
    One teachable cohort: school + grade + section + subject + academic year + teacher.
    Same teacher/material across 8A/8B/8C is modeled as three rows (lecture reuse links come later).
    """

    __tablename__ = "class_sections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id = Column(Integer, ForeignKey("school_subjects.id", ondelete="RESTRICT"), nullable=False, index=True)
    teacher_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    grade_level = Column(String(64), nullable=False)
    section = Column(String(64), nullable=False)
    academic_year = Column(String(32), nullable=False)
    display_name = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    school = relationship("School", back_populates="class_sections")
    subject = relationship("SchoolSubject", back_populates="class_sections")
    roster_entries = relationship("RosterEntry", back_populates="class_section", cascade="all, delete-orphan")
    enrollments = relationship("ClassEnrollment", back_populates="class_section", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="check_class_section_status"),
        UniqueConstraint(
            "school_id",
            "subject_id",
            "grade_level",
            "section",
            "academic_year",
            name="uq_class_section_identity",
        ),
        Index("idx_class_sections_teacher", "teacher_user_id"),
        Index("idx_class_sections_year", "academic_year"),
    )


class RosterEntry(Base):
    """
    Coordinator-provided roster line: roll number + legal name per class section.
    linked_student_user_id is set when the student self-registers and matches this row.
    """

    __tablename__ = "roster_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    class_section_id = Column(Integer, ForeignKey("class_sections.id", ondelete="CASCADE"), nullable=False, index=True)
    roll_number = Column(String(64), nullable=False)
    first_name = Column(String(128), nullable=False)
    last_name = Column(String(128), nullable=False)
    linked_student_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="expected", server_default="expected")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    class_section = relationship("ClassSection", back_populates="roster_entries")

    __table_args__ = (
        CheckConstraint(
            "status IN ('expected', 'linked', 'withdrawn')",
            name="check_roster_entry_status",
        ),
        UniqueConstraint("class_section_id", "roll_number", name="uq_roll_per_section"),
        Index("idx_roster_roll", "roll_number"),
    )


class ClassEnrollment(Base):
    """Student placed in a class section (pending / active / rejected)."""

    __tablename__ = "class_enrollments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    class_section_id = Column(Integer, ForeignKey("class_sections.id", ondelete="CASCADE"), nullable=False, index=True)
    student_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    class_section = relationship("ClassSection", back_populates="enrollments")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'active', 'rejected')",
            name="check_enrollment_status",
        ),
        UniqueConstraint("class_section_id", "student_user_id", name="uq_enrollment_student_section"),
        Index("idx_enrollments_student", "student_user_id"),
    )
