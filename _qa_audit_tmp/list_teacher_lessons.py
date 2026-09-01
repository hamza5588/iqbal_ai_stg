from app import create_app
from app.utils.db import get_db
from app.models.database_models import Lesson as L

app = create_app()
with app.app_context():
    db = get_db()
    rows = db.query(L).filter(L.teacher_id == 2).order_by(L.id.desc()).limit(15).all()
    print("COUNT", len(rows))
    for r in rows:
        preview = (r.content or "").replace("\n", " ")[:80]
        print(
            r.id,
            "|",
            (r.title or "")[:55],
            "| parent",
            r.parent_lesson_id,
            "| ver",
            r.version,
            r.version_number,
            "| lid",
            r.lesson_id,
            "| thread",
            r.rag_thread_id,
            "|",
            preview,
        )
