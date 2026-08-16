"""
Regression test for Group C (bug #24), dependency-free variant.

See tests/test_lesson_delete_visibility.py for the behavioral (in-memory DB)
version of this test. This file only checks that the fix's source pattern is
present, so it can run without SQLAlchemy installed.

Run: python tests/test_group_c_bug_fix_static.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_SRC = (ROOT / "app" / "models" / "models.py").read_text(encoding="utf-8")


def _function_body(name):
    m = re.search(rf"def {name}\(.*?\n(.*?)\n    @staticmethod", MODELS_SRC, re.S)
    if not m:
        # last function in the class won't be followed by another @staticmethod match nearby;
        # fall back to a generous slice.
        m = re.search(rf"def {name}\(.*?\n(.*?)\n\n    @staticmethod", MODELS_SRC, re.S)
    assert m, f"{name} not found in app/models/models.py"
    return m.group(1)


def test_get_public_latest_lessons_excludes_stale_versions():
    body = _function_body("get_public_latest_lessons")
    assert "DBLesson.has_child_version.is_(False)" in body, (
        "get_public_latest_lessons() must filter out non-current versions, otherwise deleting "
        "a lesson's current version leaves an older version visible to students"
    )


def test_get_public_latest_lessons_paginated_excludes_stale_versions():
    body = _function_body("get_public_latest_lessons_paginated")
    assert "DBLesson.has_child_version.is_(False)" in body


def test_search_lessons_excludes_stale_versions():
    body = _function_body("search_lessons")
    assert "DBLesson.has_child_version.is_(False)" in body


if __name__ == "__main__":
    import sys

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
    print(f"All {len(tests)} Group C static tests passed - bug #24 fix is in place.")
    sys.exit(0)
