from app import create_app
from app.utils.db import get_db
from app.models.database_models import Lesson as L

app = create_app()
with app.app_context():
    db = get_db()
    first = db.query(L).filter(L.id == 21).first()
    second = db.query(L).filter(L.id == 22).first()
    if not first or not second:
        print("MISSING", bool(first), bool(second))
        raise SystemExit(1)
    print("BEFORE", second.id, "parent", second.parent_lesson_id, "lid", second.lesson_id, "vn", second.version_number)
    second.parent_lesson_id = first.id
    second.parent_version_id = first.id
    second.lesson_id = first.lesson_id
    second.version_number = 2
    first.has_child_version = True
    db.commit()
    db.refresh(second)
    db.refresh(first)
    print("AFTER", second.id, "parent", second.parent_lesson_id, "lid", second.lesson_id, "vn", second.version_number)
    print("FIRST has_child", first.has_child_version)
    versions = (
        db.query(L)
        .filter((L.id == 21) | (L.parent_lesson_id == 21))
        .order_by(L.version_number.asc())
        .all()
    )
    for v in versions:
        heading = (v.content or "").split("\n", 1)[0]
        print("V", v.version_number, v.id, v.title, "|", heading)
