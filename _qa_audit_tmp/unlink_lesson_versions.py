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
    second.parent_lesson_id = None
    second.parent_version_id = None
    second.lesson_id = "L1787080707543"
    second.version_number = 1
    first.has_child_version = False
    db.commit()
    db.refresh(first)
    db.refresh(second)
    print("21 parent", first.parent_lesson_id, "child", first.has_child_version, "lid", first.lesson_id)
    print("22 parent", second.parent_lesson_id, "vn", second.version_number, "lid", second.lesson_id)
    print("21 heading", (first.content or "").split("\n", 1)[0])
    print("22 heading", (second.content or "").split("\n", 1)[0])
