"""
Exam Syllabus Engine service.

Manages the hierarchical syllabus tree:
  ExamType → SyllabusChapter → SyllabusTopic → SyllabusSubTopic

All deletes are soft (is_active=False). The get_syllabus() function
returns the full nested structure suitable for API responses.
"""
from __future__ import annotations

from typing import List, Optional

from app.models.phase1_models import ExamType, SyllabusChapter, SyllabusTopic, SyllabusSubTopic
from app.services.school.errors import SchoolServiceError


# ---------------------------------------------------------------------------
# Exam Types
# ---------------------------------------------------------------------------

def create_exam_type(
    db,
    *,
    name: str,
    code: str,
    description: Optional[str] = None,
) -> ExamType:
    if db.query(ExamType).filter_by(code=code).first():
        raise SchoolServiceError(f"Exam type code '{code}' already exists", "duplicate_code", 409)
    et = ExamType(name=name, code=code, description=description)
    db.add(et)
    db.flush()
    return et


def list_exam_types(db, *, active_only: bool = True) -> List[ExamType]:
    q = db.query(ExamType)
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(ExamType.name).all()


def get_exam_type(db, *, exam_type_id: int) -> ExamType:
    et = db.query(ExamType).filter_by(id=exam_type_id).first()
    if not et:
        raise SchoolServiceError("Exam type not found", "not_found", 404)
    return et


def update_exam_type(db, *, exam_type_id: int, **fields) -> ExamType:
    et = get_exam_type(db, exam_type_id=exam_type_id)
    for k, v in fields.items():
        if k in {"name", "code", "description", "is_active"}:
            setattr(et, k, v)
    db.flush()
    return et


def delete_exam_type(db, *, exam_type_id: int) -> None:
    et = get_exam_type(db, exam_type_id=exam_type_id)
    et.is_active = False
    db.flush()


# ---------------------------------------------------------------------------
# Chapters
# ---------------------------------------------------------------------------

def create_chapter(
    db,
    *,
    exam_type_id: int,
    platform_subject_id: int,
    grade: str,
    title: str,
    chapter_number: Optional[int] = None,
) -> SyllabusChapter:
    ch = SyllabusChapter(
        exam_type_id=exam_type_id,
        platform_subject_id=platform_subject_id,
        grade=grade,
        title=title,
        chapter_number=chapter_number,
    )
    db.add(ch)
    db.flush()
    return ch


def list_chapters(
    db,
    *,
    exam_type_id: int,
    platform_subject_id: int,
    grade: str,
    active_only: bool = True,
) -> List[SyllabusChapter]:
    q = db.query(SyllabusChapter).filter_by(
        exam_type_id=exam_type_id,
        platform_subject_id=platform_subject_id,
        grade=grade,
    )
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(SyllabusChapter.chapter_number, SyllabusChapter.title).all()


def get_chapter(db, *, chapter_id: int) -> SyllabusChapter:
    ch = db.query(SyllabusChapter).filter_by(id=chapter_id).first()
    if not ch:
        raise SchoolServiceError("Chapter not found", "not_found", 404)
    return ch


def update_chapter(db, *, chapter_id: int, **fields) -> SyllabusChapter:
    ch = get_chapter(db, chapter_id=chapter_id)
    for k, v in fields.items():
        if k in {"title", "chapter_number", "is_active"}:
            setattr(ch, k, v)
    db.flush()
    return ch


def delete_chapter(db, *, chapter_id: int) -> None:
    ch = get_chapter(db, chapter_id=chapter_id)
    ch.is_active = False
    db.flush()


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

def add_topic(
    db,
    *,
    chapter_id: int,
    title: str,
    order_index: int = 0,
) -> SyllabusTopic:
    get_chapter(db, chapter_id=chapter_id)  # ensure chapter exists
    t = SyllabusTopic(chapter_id=chapter_id, title=title, order_index=order_index)
    db.add(t)
    db.flush()
    return t


def get_topic(db, *, topic_id: int) -> SyllabusTopic:
    t = db.query(SyllabusTopic).filter_by(id=topic_id).first()
    if not t:
        raise SchoolServiceError("Topic not found", "not_found", 404)
    return t


def update_topic(db, *, topic_id: int, **fields) -> SyllabusTopic:
    t = get_topic(db, topic_id=topic_id)
    for k, v in fields.items():
        if k in {"title", "order_index", "is_active"}:
            setattr(t, k, v)
    db.flush()
    return t


def delete_topic(db, *, topic_id: int) -> None:
    t = get_topic(db, topic_id=topic_id)
    t.is_active = False
    db.flush()


# ---------------------------------------------------------------------------
# Sub-topics
# ---------------------------------------------------------------------------

def add_sub_topic(
    db,
    *,
    topic_id: int,
    title: str,
    order_index: int = 0,
) -> SyllabusSubTopic:
    get_topic(db, topic_id=topic_id)  # ensure topic exists
    st = SyllabusSubTopic(topic_id=topic_id, title=title, order_index=order_index)
    db.add(st)
    db.flush()
    return st


def get_sub_topic(db, *, sub_topic_id: int) -> SyllabusSubTopic:
    st = db.query(SyllabusSubTopic).filter_by(id=sub_topic_id).first()
    if not st:
        raise SchoolServiceError("Sub-topic not found", "not_found", 404)
    return st


def update_sub_topic(db, *, sub_topic_id: int, **fields) -> SyllabusSubTopic:
    st = get_sub_topic(db, sub_topic_id=sub_topic_id)
    for k, v in fields.items():
        if k in {"title", "order_index", "is_active"}:
            setattr(st, k, v)
    db.flush()
    return st


def delete_sub_topic(db, *, sub_topic_id: int) -> None:
    st = get_sub_topic(db, sub_topic_id=sub_topic_id)
    st.is_active = False
    db.flush()


# ---------------------------------------------------------------------------
# Full nested syllabus
# ---------------------------------------------------------------------------

def get_syllabus(
    db,
    *,
    exam_type_id: int,
    platform_subject_id: int,
    grade: str,
) -> dict:
    """Return the full nested syllabus tree for a given exam type / subject / grade."""
    chapters = list_chapters(
        db,
        exam_type_id=exam_type_id,
        platform_subject_id=platform_subject_id,
        grade=grade,
    )
    result = []
    for ch in chapters:
        topics_data = []
        topics = (
            db.query(SyllabusTopic)
            .filter_by(chapter_id=ch.id, is_active=True)
            .order_by(SyllabusTopic.order_index, SyllabusTopic.title)
            .all()
        )
        for t in topics:
            sub_topics = (
                db.query(SyllabusSubTopic)
                .filter_by(topic_id=t.id, is_active=True)
                .order_by(SyllabusSubTopic.order_index, SyllabusSubTopic.title)
                .all()
            )
            topics_data.append({
                "id": t.id,
                "title": t.title,
                "order_index": t.order_index,
                "sub_topics": [
                    {"id": st.id, "title": st.title, "order_index": st.order_index}
                    for st in sub_topics
                ],
            })
        result.append({
            "id": ch.id,
            "title": ch.title,
            "chapter_number": ch.chapter_number,
            "topics": topics_data,
        })
    return {
        "exam_type_id": exam_type_id,
        "platform_subject_id": platform_subject_id,
        "grade": grade,
        "chapters": result,
    }
