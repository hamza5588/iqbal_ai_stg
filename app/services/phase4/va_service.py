"""Virtual assistant cards (persisted per user)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.phase4_models import VirtualAssistantCard


def create_card(
    db: Session,
    *,
    user_id: int,
    card_type: str,
    title: str,
    body: Dict[str, Any],
    action_cta: Optional[str] = None,
    due_at: Optional[datetime] = None,
) -> VirtualAssistantCard:
    row = VirtualAssistantCard(
        user_id=int(user_id),
        card_type=card_type[:64],
        title=title[:500],
        body_json=json.dumps(body, default=str),
        action_cta=(action_cta or "")[:500] or None,
        due_at=due_at,
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_active(db: Session, *, user_id: int) -> List[VirtualAssistantCard]:
    now = datetime.utcnow()
    return (
        db.query(VirtualAssistantCard)
        .filter(
            VirtualAssistantCard.user_id == int(user_id),
            or_(
                VirtualAssistantCard.status == "active",
                and_(
                    VirtualAssistantCard.status == "snoozed",
                    VirtualAssistantCard.snooze_until.isnot(None),
                    VirtualAssistantCard.snooze_until > now,
                ),
            ),
        )
        .order_by(VirtualAssistantCard.created_at.desc())
        .limit(30)
        .all()
    )


def dismiss(db: Session, *, card_id: int, user_id: int) -> bool:
    row = (
        db.query(VirtualAssistantCard)
        .filter(VirtualAssistantCard.id == card_id, VirtualAssistantCard.user_id == int(user_id))
        .first()
    )
    if not row:
        return False
    row.status = "dismissed"
    db.commit()
    return True


def complete(db: Session, *, card_id: int, user_id: int) -> bool:
    row = (
        db.query(VirtualAssistantCard)
        .filter(VirtualAssistantCard.id == card_id, VirtualAssistantCard.user_id == int(user_id))
        .first()
    )
    if not row:
        return False
    row.status = "completed"
    db.commit()
    return True


def snooze(db: Session, *, card_id: int, user_id: int, hours: int = 24) -> bool:
    row = (
        db.query(VirtualAssistantCard)
        .filter(VirtualAssistantCard.id == card_id, VirtualAssistantCard.user_id == int(user_id))
        .first()
    )
    if not row:
        return False
    row.status = "snoozed"
    row.snooze_until = datetime.utcnow() + timedelta(hours=max(1, int(hours)))
    db.commit()
    return True


def refresh_student_cards(db: Session, *, student_user_id: int) -> int:
    """Generate VA cards from latest intelligence snapshot."""
    from app.services.phase4 import intelligence_service

    snap = intelligence_service.latest_snapshot_dict(db, student_user_id=student_user_id)
    if not snap:
        return 0
    n = 0
    if snap.get("recommendations"):
        create_card(
            db,
            user_id=student_user_id,
            card_type="study_plan",
            title="Your next focus",
            body={"recommendations": snap["recommendations"], "disclaimer": snap.get("prediction_disclaimer")},
            action_cta="/student-learning/phase4-intelligence",
        )
        n += 1
    return n
