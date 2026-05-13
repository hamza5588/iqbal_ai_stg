"""Parent alerts when pass probability drops near exam."""
from __future__ import annotations

import json
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.phase1_models import ParentStudentLink
from app.models.phase4_models import ParentRiskAlertState, StudentIntelligenceSnapshot
from app.services.notification_service import create_notification
from app.services.phase4.constants import PARENT_ALERT_DAYS_TO_EXAM, PARENT_ALERT_PASS_THRESHOLD


def maybe_alert_parents(db: Session, *, student_user_id: int) -> int:
    """If latest snapshot meets risk rule, notify linked parents. Returns notifications created."""
    snap = (
        db.query(StudentIntelligenceSnapshot)
        .filter(StudentIntelligenceSnapshot.student_user_id == student_user_id)
        .order_by(StudentIntelligenceSnapshot.computed_at.desc())
        .first()
    )
    if not snap or snap.pass_probability is None:
        return 0
    pp = float(snap.pass_probability)
    days = snap.days_to_exam
    if days is None or days > PARENT_ALERT_DAYS_TO_EXAM or pp >= PARENT_ALERT_PASS_THRESHOLD:
        return 0

    recs = json.loads(snap.recommendations_json) if snap.recommendations_json else []
    if len(recs) < 3:
        from app.services.phase4 import intelligence_service

        recs = intelligence_service._three_recommendations(weak=[], days_to_exam=days)  # noqa: SLF001

    body_lines = [
        "Plain language: your child's predicted chance of passing their target exam has dipped.",
        f"Estimated pass likelihood is about {int(round(pp * 100))}%.",
        "This is not a guarantee — it is a signal to support steady study habits.",
    ]
    for i, r in enumerate(recs[:3], 1):
        body_lines.append(f"{i}. {r.get('task', 'Study block')}")

    links = (
        db.query(ParentStudentLink)
        .filter(ParentStudentLink.student_id == student_user_id, ParentStudentLink.status == "approved")
        .all()
    )
    if not links:
        return 0
    n = 0
    for link in links:
        st = (
            db.query(ParentRiskAlertState)
            .filter_by(student_user_id=student_user_id, parent_user_id=link.parent_id)
            .first()
        )
        if not st:
            st = ParentRiskAlertState(
                student_user_id=student_user_id,
                parent_user_id=link.parent_id,
                cadence="weekly",
            )
            db.add(st)
            db.flush()
        # escalate to daily if no improvement (simplified: if still below threshold and alerted before)
        if st.last_alert_at and (datetime.utcnow() - st.last_alert_at).days < 1 and st.cadence == "daily":
            continue
        if st.last_alert_at and (datetime.utcnow() - st.last_alert_at).days < 6 and st.cadence == "weekly":
            continue

        create_notification(
            db,
            recipient_id=link.parent_id,
            title="Heads-up about exam readiness",
            message="\n".join(body_lines),
            type="phase4_parent_risk",
            action_link="/parent-dashboard",
        )
        st.last_alert_at = datetime.utcnow()
        st.last_pass_probability = snap.pass_probability
        if pp < 0.3 and st.cadence == "weekly":
            st.cadence = "daily"
        n += 1
    db.commit()
    return n
