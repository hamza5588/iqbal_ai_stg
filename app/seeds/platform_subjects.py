"""Seed the platform-level subject catalog."""
import click
from app.utils.db import get_db


_SUBJECTS = [
    {"name": "Mathematics", "short_code": "MATH", "grade_bands": "1,2,3,4,5,6,7,8,9,10,11,12"},
    {"name": "English", "short_code": "ENG", "grade_bands": "1,2,3,4,5,6,7,8,9,10,11,12"},
    {"name": "Urdu", "short_code": "URDU", "grade_bands": "1,2,3,4,5,6,7,8,9,10,11,12"},
    {"name": "Science (General)", "short_code": "SCI", "grade_bands": "1,2,3,4,5,6,7,8"},
    {"name": "Social Studies", "short_code": "SS", "grade_bands": "1,2,3,4,5,6,7,8"},
    {"name": "Physics", "short_code": "PHY", "grade_bands": "9,10,11,12"},
    {"name": "Chemistry", "short_code": "CHEM", "grade_bands": "9,10,11,12"},
    {"name": "Biology", "short_code": "BIO", "grade_bands": "9,10,11,12"},
    {"name": "Computer Science", "short_code": "CS", "grade_bands": "6,7,8,9,10,11,12"},
    {"name": "Islamiyat", "short_code": "ISLAM", "grade_bands": "1,2,3,4,5,6,7,8,9,10"},
    {"name": "Pakistan Studies", "short_code": "PAK_ST", "grade_bands": "9,10,11,12"},
    {"name": "Economics", "short_code": "ECON", "grade_bands": "11,12"},
    {"name": "History", "short_code": "HIST", "grade_bands": "9,10,11,12"},
    {"name": "Geography", "short_code": "GEO", "grade_bands": "9,10,11,12"},
]


@click.command("platform-subjects")
def seed_platform_subjects():
    """Seed the platform subject catalog (idempotent)."""
    from app.models.phase1_models import PlatformSubject
    db = get_db()
    created = 0
    for data in _SUBJECTS:
        existing = db.query(PlatformSubject).filter_by(short_code=data["short_code"]).first()
        if not existing:
            db.add(PlatformSubject(**data))
            created += 1
    db.commit()
    click.echo(f"Platform subjects seeded: {created} created.")
