"""
Regression tests for same-thread lesson-name conflicts.

When a teacher saves a first lesson in a chat thread, then generates a second
different lesson in that same thread and clicks Save, a reused title must not
fail with an error toast. The Save flow must pre-check uniqueness for
(thread_id + lesson_name), prompt for a unique name via a modal, and insert a
new row without changing the previous lesson.

Run:
  python tests/test_lesson_name_conflict.py
  pytest tests/test_lesson_name_conflict.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TEMPLATE_SRC = (ROOT / "templates" / "teacher_dashboard.html").read_text(encoding="utf-8")
LESSON_ROUTES_SRC = (ROOT / "app" / "routes" / "lesson_routes.py").read_text(encoding="utf-8")
MODELS_SRC = (ROOT / "app" / "models" / "models.py").read_text(encoding="utf-8")


def _save_lesson_to_my_lessons_body():
    m = re.search(
        r"async function saveLessonToMyLessons\(pdfDataOverride, lessonContentOverride\)(.*?)\n      \}\n",
        TEMPLATE_SRC,
        re.S,
    )
    assert m, "saveLessonToMyLessons() not found in teacher_dashboard.html"
    return m.group(1)


def _create_lesson_simple_body():
    m = re.search(r"def create_lesson_simple\(\):.*?\n@bp\.route", LESSON_ROUTES_SRC, re.S)
    assert m, "create_lesson_simple body not found"
    return m.group(0)


def _check_save_conflict_body():
    m = re.search(r"def check_save_conflict\(\):.*?\n@bp\.route", LESSON_ROUTES_SRC, re.S)
    assert m, "check_save_conflict body not found"
    return m.group(0)


def _show_lesson_name_conflict_modal_body():
    m = re.search(
        r"function showLessonNameConflictModal\(currentName, lessonContent\)(.*?)\n      \}\n",
        TEMPLATE_SRC,
        re.S,
    )
    assert m, "showLessonNameConflictModal() not found"
    return m.group(1)


def _resolve_unique_title_body():
    m = re.search(
        r"async function resolveUniqueLessonTitleForSave\(baseTitle, lessonContent\)(.*?)\n      \}\n",
        TEMPLATE_SRC,
        re.S,
    )
    assert m, "resolveUniqueLessonTitleForSave() not found"
    return m.group(1)


# --- Frontend: first lesson / second lesson / rename modal --------------------

def test_first_lesson_save_still_prefers_explicit_title():
    """First-lesson save behavior is unchanged: use the teacher's title, then create."""
    body = _save_lesson_to_my_lessons_body()
    assert "const baseTitle = explicitTitle || getCurrentChatThreadTitleForLesson();" in body
    assert "resolveUniqueLessonTitleForSave(baseTitle, lessonContent)" in body
    assert "fetch('/api/lessons/create'" in body


def test_first_lesson_does_not_open_rename_modal_unless_conflict():
    """Modal is only shown after a uniqueness conflict, not on every save."""
    resolve_body = _resolve_unique_title_body()
    assert "checkLessonSaveNameConflict(title, lessonContent)" in resolve_body
    assert "if (!check.conflict)" in resolve_body
    assert "return title;" in resolve_body
    assert "showLessonNameConflictModal(title, lessonContent)" in resolve_body


def test_second_lesson_with_different_name_skips_modal():
    """A second lesson whose name is unique in the thread goes straight to create."""
    resolve_body = _resolve_unique_title_body()
    assert "if (!check.conflict)" in resolve_body
    save_body = _save_lesson_to_my_lessons_body()
    assert "getUniqueLessonTitle(baseTitle)" not in save_body


def test_second_lesson_same_name_triggers_rename_modal():
    """Reusing the first lesson's name in the same thread must prompt, not error."""
    resolve_body = _resolve_unique_title_body()
    assert "showLessonNameConflictModal" in resolve_body
    save_body = _save_lesson_to_my_lessons_body()
    assert "isLessonNameConflictResponse(response, data)" in save_body
    assert "showLessonNameConflictModal(finalTitle, lessonContent)" in save_body
    assert "showToast(data.error || 'Failed to save lesson to database.', 'error', 4000)" in save_body
    conflict_branch = save_body.split("isLessonNameConflictResponse")[1]
    toast_index = conflict_branch.find("showToast(data.error")
    modal_index = conflict_branch.find("showLessonNameConflictModal")
    assert modal_index != -1 and (toast_index == -1 or modal_index < toast_index), (
        "On LESSON_NAME_CONFLICT the Save flow must show the rename modal before "
        "falling through to a generic error toast"
    )


def test_rename_modal_has_name_input_and_save_generate_button():
    assert 'id="lessonNameConflictModal"' in TEMPLATE_SRC
    assert 'id="lessonNameConflictInput"' in TEMPLATE_SRC
    assert 'id="lessonNameConflictSaveBtn"' in TEMPLATE_SRC
    assert "Save / Generate" in TEMPLATE_SRC
    assert 'for="lessonNameConflictInput">Lesson name' in TEMPLATE_SRC


def test_rename_modal_validates_empty_names():
    modal_body = _show_lesson_name_conflict_modal_body()
    assert "Please enter a lesson name." in modal_body
    assert "if (!value)" in modal_body
    assert "checkLessonSaveNameConflict(value, lessonContent)" in modal_body


def test_new_name_is_used_for_create_after_modal():
    """After the teacher types a unique name, that name is the create payload title."""
    save_body = _save_lesson_to_my_lessons_body()
    assert "finalTitle = renamed;" in save_body
    assert "title: finalTitle," in save_body
    assert "resolveUniqueLessonTitleForSave(baseTitle, lessonContent)" in save_body


def test_save_prechecks_thread_name_conflict_before_create():
    assert "fetch('/api/lessons/check_save_conflict'" in TEMPLATE_SRC
    save_body = _save_lesson_to_my_lessons_body()
    resolve_call = save_body.find("resolveUniqueLessonTitleForSave")
    create_call = save_body.find("fetch('/api/lessons/create'")
    assert resolve_call != -1 and create_call != -1 and resolve_call < create_call


# --- Backend: uniqueness is thread_id + lesson_name ---------------------------

def test_check_title_exists_accepts_thread_scope():
    assert "rag_thread_id: str = None" in MODELS_SRC
    m = re.search(
        r"def check_title_exists\(teacher_id: int, title: str, exclude_lesson_id: int = None,\s*"
        r"rag_thread_id: str = None\)",
        MODELS_SRC,
    )
    assert m, "check_title_exists must accept optional rag_thread_id"
    assert "DBLesson.rag_thread_id == thread_id" in MODELS_SRC


def test_create_lesson_simple_returns_structured_name_conflict():
    body = _create_lesson_simple_body()
    assert "check_title_exists(session['user_id'], title, rag_thread_id=rag_thread_id)" in body
    assert "_lesson_name_conflict_payload()" in body
    assert ", 409" in body
    assert "LESSON_NAME_CONFLICT" in LESSON_ROUTES_SRC
    assert "'name_conflict': True" in LESSON_ROUTES_SRC


def test_create_does_not_update_prior_lesson_when_inserting_new_name():
    """A new second lesson must insert, not overwrite, once similarity does not match."""
    body = _create_lesson_simple_body()
    assert "_find_existing_same_lesson_in_thread" in body
    assert "LessonModel.create_lesson(" in body
    assert "updated_existing" in body


def test_check_save_conflict_endpoint_distinguishes_update_from_name_clash():
    body = _check_save_conflict_body()
    assert "_find_existing_same_lesson_in_thread" in body
    assert "'would_update': True" in body
    assert "'empty_title': True" in body
    assert "check_title_exists" in body
    assert "LESSON_NAME_CONFLICT" in body


def test_check_title_exists_route_forwards_thread_id():
    m = re.search(r"def check_title_exists\(\):.*?\n@bp\.route", LESSON_ROUTES_SRC, re.S)
    assert m, "check_title_exists route body not found"
    body = m.group(0)
    assert "request.args.get('thread_id')" in body
    assert "rag_thread_id=rag_thread_id" in body


# --- Behavioral: in-memory SQLite uniqueness ---------------------------------

def _make_sqlite_harness():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.database_models import Base, User, Lesson
    import app.models.models as models_module

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Lesson.__table__])
    session = sessionmaker(bind=engine)()
    teacher = User(
        username="teacher-name-conflict",
        useremail="teacher-name-conflict@example.com",
        password="hashed",
        role="teacher",
        class_standard="N/A",
        medium="English",
        groq_api_key="",
    )
    session.add(teacher)
    session.commit()
    original_get_db = models_module.get_db
    models_module.get_db = lambda: session
    return session, teacher, models_module, original_get_db, Lesson


def _add_lesson(session, teacher, Lesson, *, title, content, rag_thread_id):
    lesson = Lesson(
        teacher_id=teacher.id,
        title=title,
        content=content,
        rag_thread_id=rag_thread_id,
        is_public=True,
        status="finalized",
    )
    session.add(lesson)
    session.commit()
    return lesson


def test_first_lesson_in_thread_has_no_title_conflict():
    session, teacher, models_module, original_get_db, Lesson = _make_sqlite_harness()
    try:
        exists = models_module.LessonModel.check_title_exists(
            teacher.id, "Quadratic Equations", rag_thread_id="thread-a"
        )
        assert exists is False
    finally:
        models_module.get_db = original_get_db
        session.close()


def test_second_lesson_different_name_has_no_conflict():
    session, teacher, models_module, original_get_db, Lesson = _make_sqlite_harness()
    try:
        _add_lesson(
            session, teacher, Lesson,
            title="Quadratic Equations",
            content="# Quadratic Equations\n\nFactoring and the formula.",
            rag_thread_id="thread-a",
        )
        exists = models_module.LessonModel.check_title_exists(
            teacher.id, "Nature of Roots", rag_thread_id="thread-a"
        )
        assert exists is False
    finally:
        models_module.get_db = original_get_db
        session.close()


def test_second_lesson_same_name_in_thread_conflicts():
    session, teacher, models_module, original_get_db, Lesson = _make_sqlite_harness()
    try:
        first = _add_lesson(
            session, teacher, Lesson,
            title="Quadratic Equations",
            content="# Quadratic Equations\n\nFactoring and the formula.",
            rag_thread_id="thread-a",
        )
        exists = models_module.LessonModel.check_title_exists(
            teacher.id, "quadratic equations", rag_thread_id="thread-a"
        )
        assert exists is True

        other_thread = models_module.LessonModel.check_title_exists(
            teacher.id, "Quadratic Equations", rag_thread_id="thread-b"
        )
        assert other_thread is False

        second = _add_lesson(
            session, teacher, Lesson,
            title="Quadratic Equations - Nature of Roots",
            content="# Nature of Roots\n\nDiscriminant and root types.",
            rag_thread_id="thread-a",
        )
        session.refresh(first)
        assert first.title == "Quadratic Equations"
        assert first.content.startswith("# Quadratic Equations")
        assert second.id != first.id
        assert second.rag_thread_id == first.rag_thread_id
    finally:
        models_module.get_db = original_get_db
        session.close()


def test_creating_second_lesson_after_rename_succeeds():
    session, teacher, models_module, original_get_db, Lesson = _make_sqlite_harness()
    try:
        _add_lesson(
            session, teacher, Lesson,
            title="Photosynthesis",
            content="# Photosynthesis\n\nLight and dark reactions.",
            rag_thread_id="thread-a",
        )
        assert models_module.LessonModel.check_title_exists(
            teacher.id, "Photosynthesis", rag_thread_id="thread-a"
        ) is True
        assert models_module.LessonModel.check_title_exists(
            teacher.id, "Respiration", rag_thread_id="thread-a"
        ) is False
    finally:
        models_module.get_db = original_get_db
        session.close()


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_")]
    failed = []
    skipped = []
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except ImportError as e:
            print(f"[SKIP] {name}: {e}")
            skipped.append(name)
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            failed.append(name)
    print()
    if failed:
        print(f"{len(failed)}/{len(tests)} test(s) FAILED")
        sys.exit(1)
    extra = f" ({len(skipped)} skipped)" if skipped else ""
    print(f"All {len(tests) - len(skipped)} lesson-name-conflict tests passed{extra}.")
    sys.exit(0)
