"""
Platform Content Library service.

Admins upload reference books (PDFs, docs) tagged by subject and grade.
Teachers and students can list and download these books.
Files are stored on local disk under uploaded_files/content_books/
(same pattern as UserDocument).
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

from app.models.phase1_models import ContentBook
from app.services.school.errors import SchoolServiceError

_CONTENT_BOOKS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "uploaded_files",
    "content_books",
)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def upload_content_book(
    db,
    *,
    actor_id: int,
    title: str,
    grade: str,
    file_path: str,
    file_size: Optional[int] = None,
    mime_type: Optional[str] = None,
    platform_subject_id: Optional[int] = None,
    school_subject_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> ContentBook:
    """Register a content book. The file must already be saved at file_path."""
    if not title or not grade:
        raise SchoolServiceError("title and grade are required", "validation_error", 400)

    book = ContentBook(
        platform_subject_id=platform_subject_id,
        school_subject_id=school_subject_id,
        grade=grade,
        title=title,
        file_path=file_path,
        file_size=file_size,
        mime_type=mime_type,
        metadata_json=json.dumps(metadata) if metadata else None,
        uploaded_by=actor_id,
    )
    db.add(book)
    db.flush()
    return book


def list_content_books(
    db,
    *,
    platform_subject_id: Optional[int] = None,
    school_subject_id: Optional[int] = None,
    grade: Optional[str] = None,
    is_active: bool = True,
) -> List[ContentBook]:
    q = db.query(ContentBook).filter_by(is_active=is_active)
    if platform_subject_id is not None:
        q = q.filter_by(platform_subject_id=platform_subject_id)
    if school_subject_id is not None:
        q = q.filter_by(school_subject_id=school_subject_id)
    if grade is not None:
        q = q.filter_by(grade=grade)
    return q.order_by(ContentBook.title).all()


def get_content_book(db, *, book_id: int) -> ContentBook:
    book = db.query(ContentBook).filter_by(id=book_id).first()
    if not book:
        raise SchoolServiceError("Content book not found", "not_found", 404)
    return book


def update_content_book(db, *, book_id: int, **fields) -> ContentBook:
    book = get_content_book(db, book_id=book_id)
    allowed = {"title", "grade", "platform_subject_id", "school_subject_id", "mime_type", "is_active"}
    for k, v in fields.items():
        if k in allowed:
            setattr(book, k, v)
    if "metadata" in fields and fields["metadata"] is not None:
        book.metadata_json = json.dumps(fields["metadata"])
    db.flush()
    return book


def delete_content_book(db, *, book_id: int) -> None:
    """Soft-delete a content book (is_active=False)."""
    book = get_content_book(db, book_id=book_id)
    book.is_active = False
    db.flush()


def get_upload_dir(book_id: int) -> str:
    """Return (and create) the local directory for storing a book's file."""
    path = os.path.join(_CONTENT_BOOKS_DIR, str(book_id))
    _ensure_dir(path)
    return path
