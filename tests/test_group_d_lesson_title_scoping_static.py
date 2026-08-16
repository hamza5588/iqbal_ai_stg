"""
Regression test for bug #20: lesson-title de-duplication was effectively global on a
shared browser because the localStorage keys used to track "titles this browser has
already saved" were not namespaced per teacher. The backend (LessonModel.check_title_exists)
was already correctly scoped per-teacher - only the frontend's redundant client-side
dedup logic leaked across accounts sharing a device.

Run: python tests/test_group_d_lesson_title_scoping_static.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_SRC = (ROOT / "templates" / "teacher_dashboard.html").read_text(encoding="utf-8")


def test_current_teacher_id_is_derived_from_session():
    assert "const CURRENT_TEACHER_ID = '{{ session.get(\"user_id\", \"\") }}';" in TEMPLATE_SRC


def test_teacher_scoped_storage_key_helper_exists():
    assert "function _teacherScopedStorageKey(baseKey)" in TEMPLATE_SRC
    assert "CURRENT_TEACHER_ID" in TEMPLATE_SRC


def test_used_lesson_titles_key_is_namespaced_on_read_and_write():
    assert (
        "localStorage.getItem(_teacherScopedStorageKey(LESSON_USED_TITLES_KEY))" in TEMPLATE_SRC
    )
    assert (
        "localStorage.setItem(_teacherScopedStorageKey(LESSON_USED_TITLES_KEY)" in TEMPLATE_SRC
    )


def test_save_signatures_key_is_namespaced_on_read_and_write():
    assert (
        "localStorage.getItem(_teacherScopedStorageKey(LESSON_SAVE_SIGNATURES_KEY))" in TEMPLATE_SRC
    )
    assert (
        "localStorage.setItem(_teacherScopedStorageKey(LESSON_SAVE_SIGNATURES_KEY)" in TEMPLATE_SRC
    )


def test_no_raw_unnamespaced_localstorage_access_for_lesson_dedup_keys():
    """
    None of the three dedup keys should be passed directly to localStorage.getItem/setItem
    anymore - every access must go through _teacherScopedStorageKey(...).
    """
    for key_name in ("LESSON_SAVE_SIGNATURES_KEY", "LESSON_USED_TITLES_KEY", "LESSON_SAVE_META_KEY"):
        raw_accesses = re.findall(
            rf"localStorage\.(?:get|set)Item\(\s*{key_name}\b", TEMPLATE_SRC
        )
        assert raw_accesses == [], (
            f"{key_name} is still accessed without _teacherScopedStorageKey(...) wrapping: {raw_accesses}"
        )


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_")]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
    print()
    if failed:
        print(f"{len(failed)}/{len(tests)} test(s) FAILED")
        sys.exit(1)
    print(f"All {len(tests)} tests passed - bug #20 fix is in place.")
    sys.exit(0)
