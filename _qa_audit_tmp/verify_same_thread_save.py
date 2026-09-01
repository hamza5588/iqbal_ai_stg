"""Server-side verification: second save in the same thread must insert a new row."""
import os
import sys
import uuid

from app import create_app
from app.models.database_models import RAGThread, Lesson as DBLesson
from app.models.models import LessonModel, UserModel
from app.utils.db import get_db
from app.utils.lesson_similarity import is_likely_same_lesson
from app.utils.rag_service import _sync_saved_lesson_row

EMAIL = os.environ.get("TEST_EMAIL", "")
PASSWORD = os.environ.get("TEST_PASSWORD", "")
QUADRATIC = (
    "# Lesson on Quadratic Equations\n\n"
    "A quadratic equation has the standard form $ax^2 + bx + c = 0$.\n\n"
    "## Objectives\n"
    "- Define quadratic equations\n"
    "- Solve quadratic equations using factoring and the quadratic formula\n"
    "- Understand the nature of the roots of a quadratic equation\n\n"
    "## Types of Quadratic Equations\n"
    "Standard form, vertex form, and factored form are the common presentations.\n"
)
NATURE = (
    "# Nature of Roots\n\n"
    "The discriminant is $D = b^2 - 4ac$.\n\n"
    "- If $D > 0$: two distinct real roots.\n"
    "- If $D = 0$: exactly one real root (a repeated root).\n"
    "- If $D < 0$: no real roots (complex roots).\n\n"
    "## Exercises\n"
    "1. Solve $2x^2 + 4x - 6 = 0$ using the quadratic formula.\n"
    "2. Factor $x^2 - 5x + 6 = 0$.\n"
)


def fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


def main():
    if not EMAIL or not PASSWORD:
        fail("TEST_EMAIL / TEST_PASSWORD not set")

    app = create_app()
    with app.app_context():
        user = UserModel.get_user_by_email(EMAIL)
        if not user:
            fail("teacher account not found: %s" % EMAIL)
        if user.get("password") != PASSWORD:
            fail("teacher password did not match")
        teacher_id = user["id"]
        print("LOGIN_OK", EMAIL, "user_id", teacher_id)

        db = get_db()
        thread_id = "verify-same-thread-" + uuid.uuid4().hex[:12]
        db.add(RAGThread(user_id=teacher_id, thread_id=thread_id, name="verify-same-thread"))
        db.commit()
        print("THREAD", thread_id)

        client = app.test_client()
        headers = {
            "X-Forwarded-Proto": "https",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        login = client.post(
            "/auth/login",
            data={"useremail": EMAIL, "password": PASSWORD},
            headers=headers,
        )
        if login.status_code not in (200, 302):
            fail("login HTTP %s %s" % (login.status_code, login.get_data(as_text=True)[:300]))
        print("HTTP_LOGIN", login.status_code)

        save1 = client.post(
            "/api/lessons/create",
            json={
                "title": "[VERIFY] Quadratic Equations",
                "content": QUADRATIC,
                "focus_area": "Math",
                "grade_level": "Grade 10",
                "rag_thread_id": thread_id,
                "thread_id": thread_id,
            },
            headers=headers,
        )
        body1 = save1.get_json(silent=True) or {}
        if save1.status_code != 200 or not body1.get("success"):
            fail("first save HTTP %s %s" % (save1.status_code, body1))
        id1 = body1.get("id")
        print("SAVE1", id1, "updated_existing", body1.get("updated_existing"))

        # Old bug: generating the second lesson synced over the first My Lessons row.
        _sync_saved_lesson_row(thread_id, NATURE)
        row1 = LessonModel.get_lesson_by_id(id1)
        if not row1:
            fail("first lesson row disappeared after sync")
        if "Quadratic Equations" not in (row1.get("content") or ""):
            fail("first lesson was overwritten by sync: %r" % (row1.get("title"),))
        if "Discriminant" in (row1.get("content") or ""):
            fail("first lesson content was replaced with nature-of-roots during sync")
        print("SYNC_DID_NOT_OVERWRITE id", id1)

        save2 = client.post(
            "/api/lessons/create",
            json={
                "title": "[VERIFY] Nature of Roots",
                "content": NATURE,
                "focus_area": "Math",
                "grade_level": "Grade 10",
                "rag_thread_id": thread_id,
                "thread_id": thread_id,
            },
            headers=headers,
        )
        body2 = save2.get_json(silent=True) or {}
        if save2.status_code != 200 or not body2.get("success"):
            fail("second save HTTP %s %s" % (save2.status_code, body2))
        id2 = body2.get("id")
        print("SAVE2", id2, "updated_existing", body2.get("updated_existing"))
        if body2.get("updated_existing"):
            fail("second save updated the existing row instead of inserting a new one")
        if id2 == id1:
            fail("second save reused the first lesson id")

        rows = LessonModel.get_lessons_by_rag_thread_id(teacher_id, thread_id)
        titles = sorted(r.get("title") or "" for r in rows)
        print("ROWS", len(rows), titles)
        if len(rows) != 2:
            fail("expected 2 rows for thread, got %s" % len(rows))
        if not is_likely_same_lesson(QUADRATIC, QUADRATIC) or is_likely_same_lesson(QUADRATIC, NATURE):
            fail("similarity helper misclassified the two lessons")

        row1_after = LessonModel.get_lesson_by_id(id1)
        row2_after = LessonModel.get_lesson_by_id(id2)
        if "Quadratic Equations" not in (row1_after.get("content") or ""):
            fail("quadratic row lost its content after second save")
        if "Nature of Roots" not in (row2_after.get("content") or ""):
            fail("nature-of-roots row does not have the new content")

        print("PASS two distinct My Lessons rows for the same thread")
        print("KEEP_ROWS", id1, id2, "thread", thread_id)


if __name__ == "__main__":
    main()
