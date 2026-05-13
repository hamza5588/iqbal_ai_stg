"""Teacher-created group study slots and student RSVPs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.phase3_models import GroupStudyRsvp, GroupStudySlot
from app.models.school_learning_models import LectureClassSection
from app.models.school_org_models import ClassEnrollment, ClassSection
from app.services.phase3.access import teacher_owns_lesson


def _slot_dict(
    slot: GroupStudySlot,
    *,
    rsvp_count: int,
    student_status: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": slot.id,
        "teacher_user_id": slot.teacher_user_id,
        "lesson_id": slot.lesson_id,
        "title": slot.title,
        "starts_at": slot.starts_at.isoformat() if slot.starts_at else None,
        "ends_at": slot.ends_at.isoformat() if slot.ends_at else None,
        "max_students": slot.max_students,
        "notes": slot.notes,
        "status": slot.status,
        "rsvp_count": rsvp_count,
        "my_rsvp": student_status,
        "created_at": slot.created_at.isoformat() if slot.created_at else None,
    }


def student_may_access_slot(db: Session, *, student_user_id: int, slot: GroupStudySlot) -> bool:
    """Student must be enrolled in a section tied to the lesson, or any section with this teacher."""
    sid = int(student_user_id)
    if slot.lesson_id:
        sec_ids = [
            r[0]
            for r in db.query(LectureClassSection.class_section_id)
            .filter(LectureClassSection.lesson_id == int(slot.lesson_id))
            .all()
        ]
        if not sec_ids:
            return False
        enr = (
            db.query(ClassEnrollment)
            .filter(
                ClassEnrollment.student_user_id == sid,
                ClassEnrollment.class_section_id.in_(sec_ids),
                ClassEnrollment.status == "active",
            )
            .first()
        )
        return enr is not None

    enr = (
        db.query(ClassEnrollment)
        .join(ClassSection, ClassSection.id == ClassEnrollment.class_section_id)
        .filter(
            ClassEnrollment.student_user_id == sid,
            ClassSection.teacher_user_id == slot.teacher_user_id,
            ClassEnrollment.status == "active",
            ClassSection.status == "active",
        )
        .first()
    )
    return enr is not None


def create_slot(
    db: Session,
    *,
    teacher_user_id: int,
    lesson_id: Optional[int],
    title: str,
    starts_at: datetime,
    ends_at: datetime,
    max_students: int = 8,
    notes: Optional[str] = None,
) -> GroupStudySlot:
    if lesson_id is not None and not teacher_owns_lesson(
        db, teacher_user_id=int(teacher_user_id), lesson_id=int(lesson_id)
    ):
        raise PermissionError("You do not own this lesson.")
    if ends_at <= starts_at:
        raise ValueError("ends_at must be after starts_at")
    row = GroupStudySlot(
        teacher_user_id=int(teacher_user_id),
        lesson_id=int(lesson_id) if lesson_id is not None else None,
        title=title.strip()[:500] or "Group study",
        starts_at=starts_at,
        ends_at=ends_at,
        max_students=max(1, min(50, int(max_students))),
        notes=(notes or "").strip()[:8000] or None,
        status="scheduled",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_slots_for_teacher(db: Session, *, teacher_user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    rows = (
        db.query(GroupStudySlot)
        .filter(GroupStudySlot.teacher_user_id == int(teacher_user_id))
        .order_by(GroupStudySlot.starts_at.desc())
        .limit(limit)
        .all()
    )
    out: List[Dict[str, Any]] = []
    for slot in rows:
        cnt = (
            db.query(func.count(GroupStudyRsvp.id))
            .filter(
                GroupStudyRsvp.slot_id == slot.id,
                GroupStudyRsvp.status == "confirmed",
            )
            .scalar()
            or 0
        )
        out.append(_slot_dict(slot, rsvp_count=int(cnt)))
    return out


def list_slots_for_student(db: Session, *, student_user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """Return upcoming slots this student is allowed to join."""
    sid = int(student_user_id)
    now = datetime.utcnow()
    slots = (
        db.query(GroupStudySlot)
        .filter(GroupStudySlot.status == "scheduled", GroupStudySlot.starts_at >= now)
        .order_by(GroupStudySlot.starts_at.asc())
        .limit(200)
        .all()
    )
    out: List[Dict[str, Any]] = []
    for slot in slots:
        if not student_may_access_slot(db, student_user_id=sid, slot=slot):
            continue
        cnt = (
            db.query(func.count(GroupStudyRsvp.id))
            .filter(
                GroupStudyRsvp.slot_id == slot.id,
                GroupStudyRsvp.status == "confirmed",
            )
            .scalar()
            or 0
        )
        mine = (
            db.query(GroupStudyRsvp)
            .filter(
                GroupStudyRsvp.slot_id == slot.id,
                GroupStudyRsvp.student_user_id == sid,
            )
            .first()
        )
        st = mine.status if mine else None
        out.append(_slot_dict(slot, rsvp_count=int(cnt), student_status=st))
        if len(out) >= limit:
            break
    return out


def rsvp_student(db: Session, *, student_user_id: int, slot_id: int) -> Dict[str, Any]:
    slot = db.query(GroupStudySlot).filter(GroupStudySlot.id == int(slot_id)).first()
    if not slot or slot.status != "scheduled":
        raise ValueError("Slot not available")
    if not student_may_access_slot(db, student_user_id=int(student_user_id), slot=slot):
        raise PermissionError("Not eligible for this session")
    cnt = (
        db.query(func.count(GroupStudyRsvp.id))
        .filter(
            GroupStudyRsvp.slot_id == slot.id,
            GroupStudyRsvp.status == "confirmed",
        )
        .scalar()
        or 0
    )
    if int(cnt) >= int(slot.max_students):
        raise ValueError("Session is full")

    row = (
        db.query(GroupStudyRsvp)
        .filter(
            GroupStudyRsvp.slot_id == slot.id,
            GroupStudyRsvp.student_user_id == int(student_user_id),
        )
        .first()
    )
    if row:
        row.status = "confirmed"
    else:
        row = GroupStudyRsvp(
            slot_id=slot.id,
            student_user_id=int(student_user_id),
            status="confirmed",
        )
        db.add(row)
    db.commit()
    return {"ok": True, "slot_id": slot.id}


def cancel_rsvp(db: Session, *, student_user_id: int, slot_id: int) -> bool:
    row = (
        db.query(GroupStudyRsvp)
        .filter(
            GroupStudyRsvp.slot_id == int(slot_id),
            GroupStudyRsvp.student_user_id == int(student_user_id),
        )
        .first()
    )
    if not row:
        return False
    row.status = "cancelled"
    db.commit()
    return True


def cancel_slot_teacher(db: Session, *, teacher_user_id: int, slot_id: int) -> bool:
    slot = (
        db.query(GroupStudySlot)
        .filter(
            GroupStudySlot.id == int(slot_id),
            GroupStudySlot.teacher_user_id == int(teacher_user_id),
        )
        .first()
    )
    if not slot:
        return False
    slot.status = "cancelled"
    db.commit()
    return True
