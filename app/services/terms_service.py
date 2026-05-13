"""
Terms of Service service.

Tracks which version each user has accepted and provides the
pass-probability disclaimer constant used across the platform.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.exc import IntegrityError

from app.models.phase1_models import TermsAcceptance
from app.models.database_models import User

CURRENT_TERMS_VERSION: str = "1.0"

PASS_PROBABILITY_DISCLAIMER: str = (
    "AI-generated pass probability estimates are indicative only and do not "
    "guarantee exam results. Actual outcomes depend on many factors outside "
    "this platform. Use these predictions as one of several tools when planning "
    "your studies."
)


def record_acceptance(
    db,
    *,
    user_id: int,
    ip_address: Optional[str] = None,
    version: str = CURRENT_TERMS_VERSION,
) -> TermsAcceptance:
    """
    Record that a user has accepted the given terms version.
    Idempotent: if the row already exists, returns the existing record.
    Also updates user.terms_accepted_version for the fast check.
    """
    existing = db.query(TermsAcceptance).filter_by(user_id=user_id, terms_version=version).first()
    if existing:
        return existing

    record = TermsAcceptance(user_id=user_id, terms_version=version, ip_address=ip_address)
    db.add(record)

    # Update denormalized fast-check on the user row
    user = db.query(User).filter_by(id=user_id).first()
    if user:
        user.terms_accepted_version = version

    db.flush()
    return record


def has_accepted(db, *, user_id: int, version: str = CURRENT_TERMS_VERSION) -> bool:
    """Return True if the user has accepted the given (or current) terms version."""
    return (
        db.query(TermsAcceptance).filter_by(user_id=user_id, terms_version=version).count() > 0
    )


def requires_acceptance(user: User, version: str = CURRENT_TERMS_VERSION) -> bool:
    """
    Fast check using the denormalized field on User.
    Returns True when the user must accept terms before proceeding.
    """
    return user.terms_accepted_version != version
