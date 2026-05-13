"""AI pedagogy template proposals and admin approval."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.phase4_models import AIPedagogyTemplate, AIPedagogyTemplateProposal, Phase4AdminAuditLog


def list_pending_proposals(db: Session, *, limit: int = 50) -> List[AIPedagogyTemplateProposal]:
    return (
        db.query(AIPedagogyTemplateProposal)
        .filter(AIPedagogyTemplateProposal.status == "pending")
        .order_by(AIPedagogyTemplateProposal.created_at.desc())
        .limit(limit)
        .all()
    )


def approve_proposal(
    db: Session, *, proposal_id: int, reviewer_user_id: int, edited_body: Optional[str] = None
) -> AIPedagogyTemplate:
    prop = db.query(AIPedagogyTemplateProposal).filter(AIPedagogyTemplateProposal.id == proposal_id).first()
    if not prop or prop.status != "pending":
        raise ValueError("invalid_proposal")
    body = (edited_body or prop.proposed_body).strip()
    tpl = (
        db.query(AIPedagogyTemplate)
        .filter(AIPedagogyTemplate.syllabus_topic_id == prop.syllabus_topic_id)
        .first()
    )
    if not tpl:
        tpl = AIPedagogyTemplate(syllabus_topic_id=prop.syllabus_topic_id, template_key="default", body=body)
        db.add(tpl)
    else:
        tpl.body = body
        tpl.version = int(tpl.version or 1) + 1
    prop.status = "approved"
    prop.reviewer_user_id = reviewer_user_id
    prop.reviewed_at = datetime.utcnow()
    db.add(
        Phase4AdminAuditLog(
            actor_user_id=reviewer_user_id,
            action="pedagogy_proposal_approve",
            entity_type="AIPedagogyTemplateProposal",
            entity_id=proposal_id,
            detail_json=json.dumps({"syllabus_topic_id": prop.syllabus_topic_id}, default=str),
        )
    )
    db.commit()
    db.refresh(tpl)
    return tpl


def reject_proposal(db: Session, *, proposal_id: int, reviewer_user_id: int, reason: str = "") -> None:
    prop = db.query(AIPedagogyTemplateProposal).filter(AIPedagogyTemplateProposal.id == proposal_id).first()
    if not prop:
        raise ValueError("invalid_proposal")
    prop.status = "rejected"
    prop.reviewer_user_id = reviewer_user_id
    prop.reviewed_at = datetime.utcnow()
    prop.critique_json = json.dumps({"reason": reason}, default=str)
    db.add(
        Phase4AdminAuditLog(
            actor_user_id=reviewer_user_id,
            action="pedagogy_proposal_reject",
            entity_type="AIPedagogyTemplateProposal",
            entity_id=proposal_id,
            detail_json=json.dumps({"reason": reason}, default=str),
        )
    )
    db.commit()


def create_proposal_from_scan(
    db: Session, *, syllabus_topic_id: int, proposed_body: str, critique: Optional[Dict[str, Any]] = None
) -> AIPedagogyTemplateProposal:
    row = AIPedagogyTemplateProposal(
        syllabus_topic_id=int(syllabus_topic_id),
        proposed_body=proposed_body,
        status="pending",
        critique_json=json.dumps(critique or {}, default=str),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
