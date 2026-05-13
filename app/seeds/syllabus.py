"""Seed a minimal sample syllabus (Grade 8 Mathematics / Board Exam)."""
import click
from app.utils.db import get_db


@click.command("syllabus")
def seed_syllabus():
    """Seed a minimal sample syllabus (idempotent)."""
    from app.models.phase1_models import (
        ExamType, PlatformSubject, SyllabusChapter, SyllabusTopic, SyllabusSubTopic
    )
    db = get_db()

    board = db.query(ExamType).filter_by(code="BOARD").first()
    math = db.query(PlatformSubject).filter_by(short_code="MATH").first()

    if not board or not math:
        click.echo("Run 'flask seed exam-types' and 'flask seed platform-subjects' first.")
        return

    sample_chapters = [
        {
            "title": "Sets and Functions",
            "chapter_number": 1,
            "topics": [
                {"title": "Introduction to Sets", "sub_topics": ["Definition of a Set", "Types of Sets", "Set Notation"]},
                {"title": "Operations on Sets", "sub_topics": ["Union", "Intersection", "Difference", "Complement"]},
                {"title": "Functions", "sub_topics": ["Definition", "Domain and Range", "Types of Functions"]},
            ],
        },
        {
            "title": "Real Numbers and Algebra",
            "chapter_number": 2,
            "topics": [
                {"title": "Real Number System", "sub_topics": ["Rational Numbers", "Irrational Numbers", "Number Line"]},
                {"title": "Algebraic Expressions", "sub_topics": ["Polynomials", "Factorisation", "HCF and LCM"]},
            ],
        },
        {
            "title": "Linear Equations",
            "chapter_number": 3,
            "topics": [
                {"title": "Equations in One Variable", "sub_topics": ["Simple Equations", "Word Problems"]},
                {"title": "Simultaneous Equations", "sub_topics": ["Elimination Method", "Substitution Method", "Graphical Method"]},
            ],
        },
    ]

    created_chapters = 0
    for ch_data in sample_chapters:
        existing_ch = db.query(SyllabusChapter).filter_by(
            exam_type_id=board.id,
            platform_subject_id=math.id,
            grade="8",
            chapter_number=ch_data["chapter_number"],
        ).first()
        if existing_ch:
            continue

        ch = SyllabusChapter(
            exam_type_id=board.id,
            platform_subject_id=math.id,
            grade="8",
            title=ch_data["title"],
            chapter_number=ch_data["chapter_number"],
        )
        db.add(ch)
        db.flush()
        created_chapters += 1

        for t_idx, t_data in enumerate(ch_data["topics"]):
            t = SyllabusTopic(chapter_id=ch.id, title=t_data["title"], order_index=t_idx)
            db.add(t)
            db.flush()
            for st_idx, st_title in enumerate(t_data["sub_topics"]):
                st = SyllabusSubTopic(topic_id=t.id, title=st_title, order_index=st_idx)
                db.add(st)

    db.commit()
    click.echo(f"Sample syllabus seeded: {created_chapters} chapters created for Grade 8 Mathematics / Board Exam.")
