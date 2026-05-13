"""Seed default exam types."""
import click
from app.utils.db import get_db


_EXAM_TYPES = [
    {"code": "BOARD", "name": "Board Exam", "description": "Annual board examination (matric/intermediate level)"},
    {"code": "MID_TERM", "name": "Mid-Term Exam", "description": "Mid-semester internal examination"},
    {"code": "UNIT_TEST", "name": "Unit Test", "description": "Short unit-level assessment"},
    {"code": "MOCK", "name": "Mock Exam", "description": "Practice exam simulating board conditions"},
    {"code": "QUARTERLY", "name": "Quarterly Exam", "description": "End-of-quarter assessment"},
    {"code": "ANNUAL", "name": "Annual Exam", "description": "End-of-year internal examination"},
]


@click.command("exam-types")
def seed_exam_types():
    """Seed default exam types (idempotent)."""
    from app.models.phase1_models import ExamType
    db = get_db()
    created = 0
    for data in _EXAM_TYPES:
        existing = db.query(ExamType).filter_by(code=data["code"]).first()
        if not existing:
            db.add(ExamType(**data))
            created += 1
    db.commit()
    click.echo(f"Exam types seeded: {created} created.")
