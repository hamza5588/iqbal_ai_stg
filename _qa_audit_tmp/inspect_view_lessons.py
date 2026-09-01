from app import create_app
from app.models.models import LessonModel

app = create_app()
with app.app_context():
    for lid in (21, 22):
        lesson = LessonModel.get_lesson_by_id(lid)
        versions = LessonModel.get_lesson_versions(lid)
        print("=" * 60)
        print("ID", lid)
        print("TITLE", lesson.get("title"))
        print("PARENT", lesson.get("parent_lesson_id"))
        print("VER", lesson.get("version"), lesson.get("version_number"))
        print("N_VERSIONS", len(versions), [v.get("id") for v in versions])
        content = lesson.get("content") or ""
        print("CONTENT_LEN", len(content))
        print("HEADING", content.split("\n", 1)[0])
        print("HAS_DISCRIMINANT", "discriminant" in content.lower() or "D = b" in content or "D=b" in content)
        print("HAS_QUADRATIC_TITLE", "Lesson on Quadratic Equations" in content)
        print("HAS_NATURE_TITLE", "Nature of Roots" in content)
