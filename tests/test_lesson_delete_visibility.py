"""
Regression test for Group C (bug #24): deleting a lesson's current version
must not leave an older version of that same logical lesson visible to
students.

Root cause: the teacher-side "current version" of a lesson is defined by
has_child_version == False (Lesson.has_child_version, app/models/database_models.py),
but the student-side "latest per lesson" queries in app/models/models.py
(get_public_latest_lessons / get_public_latest_lessons_paginated / search_lessons)
picked whichever remaining is_public row had the max(created_at) - ignoring
has_child_version entirely. Deleting only the current (has_child_version=False)
version left the older version (still is_public=True, has_child_version=True)
as the "latest remaining" row and still served it to students.

Uses an in-memory SQLite DB and monkeypatches app.models.models.get_db so this
runs without a real Postgres/Milvus stack - only needs SQLAlchemy installed.
"""
from datetime import datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database_models import Base, User, Lesson
import app.models.models as models_module


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Lesson.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def teacher(db_session):
    user = User(
        username="teacher1",
        useremail="teacher1@example.com",
        password="hashed",
        role="teacher",
        class_standard="N/A",
        medium="English",
        groq_api_key="",
    )
    db_session.add(user)
    db_session.commit()
    return user


def _make_lesson(db_session, teacher, *, lesson_id, version_number, has_child_version, created_at):
    lesson = Lesson(
        teacher_id=teacher.id,
        title=f"Lesson v{version_number}",
        content="content",
        lesson_id=lesson_id,
        version_number=version_number,
        has_child_version=has_child_version,
        is_public=True,
        created_at=created_at,
    )
    db_session.add(lesson)
    db_session.commit()
    return lesson


def test_deleting_current_version_hides_lesson_from_students(db_session, teacher, monkeypatch):
    monkeypatch.setattr(models_module, "get_db", lambda: db_session)

    now = datetime.utcnow()
    v1 = _make_lesson(
        db_session, teacher,
        lesson_id="lesson-abc", version_number=1,
        has_child_version=True,  # a newer version (v2) exists
        created_at=now - timedelta(minutes=5),
    )
    v2 = _make_lesson(
        db_session, teacher,
        lesson_id="lesson-abc", version_number=2,
        has_child_version=False,  # v2 is the current tip
        created_at=now,
    )

    # Before delete: students should see v2, the current version - not v1.
    before = models_module.LessonModel.get_public_latest_lessons_paginated(page=1, per_page=10)
    ids_before = {l["id"] for l in before["lessons"]}
    assert ids_before == {v2.id}

    # Teacher deletes v2 - mirrors LessonModel.delete_lesson(), a plain row delete.
    db_session.delete(v2)
    db_session.commit()

    after = models_module.LessonModel.get_public_latest_lessons_paginated(page=1, per_page=10)
    ids_after = {l["id"] for l in after["lessons"]}
    assert v1.id not in ids_after, (
        "v1 still has has_child_version=True and must stay excluded from student results "
        "even though it's now the only remaining row for lesson-abc"
    )
    assert ids_after == set()


def test_get_public_latest_lessons_also_excludes_stale_older_version(db_session, teacher, monkeypatch):
    """Same scenario via the non-paginated get_public_latest_lessons()."""
    monkeypatch.setattr(models_module, "get_db", lambda: db_session)

    now = datetime.utcnow()
    v1 = _make_lesson(
        db_session, teacher,
        lesson_id="lesson-xyz", version_number=1,
        has_child_version=True,
        created_at=now - timedelta(minutes=5),
    )
    v2 = _make_lesson(
        db_session, teacher,
        lesson_id="lesson-xyz", version_number=2,
        has_child_version=False,
        created_at=now,
    )
    db_session.delete(v2)
    db_session.commit()

    results = models_module.LessonModel.get_public_latest_lessons()
    ids = {l["id"] for l in results}
    assert v1.id not in ids
