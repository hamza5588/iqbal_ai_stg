"""Grade-scoped lesson publication visibility tests."""
from datetime import datetime

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database_models import Base, Lesson, User
from app.models.lms_models import ClassEnrollment, SchoolClass
import app.models.models as models_module
from app.services.lms import class_service


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Lesson.__table__,
            SchoolClass.__table__,
            ClassEnrollment.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db, username, role, grade):
    user = User(
        username=username,
        useremail=f"{username}@example.com",
        password="hashed",
        role=role,
        class_standard=grade,
        medium="English",
        groq_api_key="",
    )
    db.add(user)
    db.commit()
    return user


def _lesson(db, teacher, title, grade, published=True):
    lesson = Lesson(
        teacher_id=teacher.id,
        title=title,
        content="content",
        grade_level=grade,
        lesson_id=title.lower().replace(" ", "-"),
        has_child_version=False,
        is_public=published,
        created_at=datetime.utcnow(),
    )
    db.add(lesson)
    db.commit()
    return lesson


def test_student_only_receives_published_lessons_from_linked_teacher_and_grade(
    db_session, monkeypatch
):
    monkeypatch.setattr(models_module, "get_db", lambda: db_session)
    monkeypatch.setattr(class_service, "get_db", lambda: db_session)

    linked_teacher = _user(db_session, "linked_teacher", "teacher", "8,9")
    other_teacher = _user(db_session, "other_teacher", "teacher", "8")
    student = _user(db_session, "student", "student", "8")

    school_class = SchoolClass(
        teacher_id=linked_teacher.id,
        name="Grade 8",
        join_code="GRADE8AA",
        grade_level="8",
        is_active=True,
    )
    db_session.add(school_class)
    db_session.commit()
    db_session.add(ClassEnrollment(class_id=school_class.id, student_id=student.id, status="active"))

    visible = _lesson(db_session, linked_teacher, "Visible", "8th Grade")
    _lesson(db_session, linked_teacher, "Wrong grade", "9")
    _lesson(db_session, linked_teacher, "Draft", "8", published=False)
    _lesson(db_session, other_teacher, "Other teacher", "8")
    db_session.commit()

    links = class_service.student_teacher_grade_links(student.id)
    result = models_module.LessonModel.get_public_latest_lessons_paginated(
        teacher_grade_links=links,
        page=1,
        per_page=20,
    )

    assert links == [(linked_teacher.id, "8")]
    assert [lesson["id"] for lesson in result["lessons"]] == [visible.id]

