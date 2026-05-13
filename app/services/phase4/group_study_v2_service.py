"""Phase 4 study groups (distinct from teacher GroupStudySlot calendar)."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.phase4_models import (
    StudyGroup,
    StudyGroupAIMessage,
    StudyGroupAIThread,
    StudyGroupInvite,
    StudyGroupMember,
    StudyGroupNotes,
    TeacherGroupSuggestion,
)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_group(
    db: Session,
    *,
    creator_user_id: int,
    name: str,
    purpose: Optional[str] = None,
    school_id: Optional[int] = None,
) -> StudyGroup:
    g = StudyGroup(
        name=name.strip()[:500] or "Study group",
        purpose=(purpose or "").strip()[:255] or None,
        school_id=school_id,
        created_by_user_id=int(creator_user_id),
        status="active",
    )
    db.add(g)
    db.flush()
    m = StudyGroupMember(group_id=g.id, user_id=int(creator_user_id), role="owner")
    db.add(m)
    # shared AI thread
    db.add(StudyGroupAIThread(group_id=g.id, scope="shared", user_id=None))
    # private thread
    db.add(StudyGroupAIThread(group_id=g.id, scope="private", user_id=int(creator_user_id)))
    db.add(StudyGroupNotes(group_id=g.id, body_text="", version=1, updated_by_user_id=int(creator_user_id)))
    db.commit()
    db.refresh(g)
    return g


def _is_member(db: Session, *, group_id: int, user_id: int) -> bool:
    return (
        db.query(StudyGroupMember)
        .filter(StudyGroupMember.group_id == group_id, StudyGroupMember.user_id == int(user_id))
        .first()
        is not None
    )


def create_invite(
    db: Session,
    *,
    group_id: int,
    actor_user_id: int,
    expires_hours: int = 72,
    max_uses: int = 50,
) -> Dict[str, Any]:
    if not _is_member(db, group_id=group_id, user_id=actor_user_id):
        raise PermissionError("not a member")
    raw = secrets.token_urlsafe(24)
    inv = StudyGroupInvite(
        group_id=group_id,
        token_hash=_hash_token(raw),
        expires_at=datetime.utcnow() + timedelta(hours=expires_hours),
        max_uses=max(1, int(max_uses)),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return {"invite_id": inv.id, "token": raw, "expires_at": inv.expires_at.isoformat()}


def join_with_token(db: Session, *, user_id: int, token: str) -> StudyGroupMember:
    th = _hash_token(token.strip())
    inv = db.query(StudyGroupInvite).filter(StudyGroupInvite.token_hash == th).first()
    if not inv:
        raise ValueError("invalid_token")
    if inv.expires_at < datetime.utcnow():
        raise ValueError("expired")
    if inv.use_count >= inv.max_uses:
        raise ValueError("max_uses")
    if _is_member(db, group_id=inv.group_id, user_id=user_id):
        return db.query(StudyGroupMember).filter_by(group_id=inv.group_id, user_id=user_id).first()
    inv.use_count = int(inv.use_count or 0) + 1
    m = StudyGroupMember(group_id=inv.group_id, user_id=int(user_id), role="member")
    db.add(m)
    db.add(StudyGroupAIThread(group_id=inv.group_id, scope="private", user_id=int(user_id)))
    db.commit()
    db.refresh(m)
    return m


def get_or_create_shared_thread(db: Session, *, group_id: int) -> StudyGroupAIThread:
    row = (
        db.query(StudyGroupAIThread)
        .filter(StudyGroupAIThread.group_id == group_id, StudyGroupAIThread.scope == "shared")
        .first()
    )
    if row:
        return row
    row = StudyGroupAIThread(group_id=group_id, scope="shared", user_id=None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_private_thread(db: Session, *, group_id: int, user_id: int) -> StudyGroupAIThread:
    row = (
        db.query(StudyGroupAIThread)
        .filter(
            StudyGroupAIThread.group_id == group_id,
            StudyGroupAIThread.scope == "private",
            StudyGroupAIThread.user_id == int(user_id),
        )
        .first()
    )
    if not row:
        row = StudyGroupAIThread(group_id=group_id, scope="private", user_id=int(user_id))
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def append_ai_message(
    db: Session,
    *,
    thread_id: int,
    sender_user_id: Optional[int],
    role: str,
    content: str,
    sources: Optional[Dict[str, Any]] = None,
) -> StudyGroupAIMessage:
    msg = StudyGroupAIMessage(
        thread_id=thread_id,
        sender_user_id=sender_user_id,
        role=role,
        content=content,
        sources_json=json.dumps(sources or {}, default=str),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def update_notes(
    db: Session,
    *,
    group_id: int,
    user_id: int,
    body_text: str,
    expected_version: int,
) -> StudyGroupNotes:
    if not _is_member(db, group_id=group_id, user_id=user_id):
        raise PermissionError("not a member")
    notes = db.query(StudyGroupNotes).filter(StudyGroupNotes.group_id == group_id).first()
    if not notes:
        notes = StudyGroupNotes(group_id=group_id, body_text="", version=1)
        db.add(notes)
        db.flush()
    if int(notes.version) != int(expected_version):
        raise ValueError("version_conflict")
    notes.body_text = body_text
    notes.version = int(notes.version) + 1
    notes.updated_by_user_id = int(user_id)
    db.commit()
    db.refresh(notes)
    return notes


def list_my_groups(db: Session, *, user_id: int) -> List[Dict[str, Any]]:
    mids = [m.group_id for m in db.query(StudyGroupMember).filter_by(user_id=user_id).all()]
    if not mids:
        return []
    groups = db.query(StudyGroup).filter(StudyGroup.id.in_(mids), StudyGroup.status == "active").all()
    return [{"id": g.id, "name": g.name, "purpose": g.purpose} for g in groups]


def create_teacher_suggestion(
    db: Session,
    *,
    teacher_user_id: int,
    class_section_id: Optional[int],
    payload: Dict[str, Any],
) -> TeacherGroupSuggestion:
    row = TeacherGroupSuggestion(
        teacher_user_id=int(teacher_user_id),
        class_section_id=class_section_id,
        payload_json=json.dumps(payload, default=str),
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def accept_suggestion(
    db: Session,
    *,
    suggestion_id: int,
    teacher_user_id: int,
    group_name: str,
) -> StudyGroup:
    s = (
        db.query(TeacherGroupSuggestion)
        .filter(
            TeacherGroupSuggestion.id == suggestion_id,
            TeacherGroupSuggestion.teacher_user_id == int(teacher_user_id),
            TeacherGroupSuggestion.status == "pending",
        )
        .first()
    )
    if not s:
        raise ValueError("not_found")
    g = create_group(db, creator_user_id=int(teacher_user_id), name=group_name, purpose="teacher_suggested")
    s.status = "accepted"
    s.resolved_study_group_id = g.id
    db.commit()
    return g
