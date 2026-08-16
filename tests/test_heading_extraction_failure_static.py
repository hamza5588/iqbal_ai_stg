"""
Dependency-free regression test for bug #21 (PDF heading extraction silent failure).
See tests/test_heading_extraction_failure.py for the behavioral version.

Run: python tests/test_heading_extraction_failure_static.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAG_SERVICE_SRC = (ROOT / "app" / "utils" / "rag_service.py").read_text(encoding="utf-8")
CONFIG_SRC = (ROOT / "app" / "config.py").read_text(encoding="utf-8")


def _extract_topics_body():
    m = re.search(
        r"def _extract_topics_with_ai\(.*?-> dict:(.*?)\ndef _persist_headings_for_thread",
        RAG_SERVICE_SRC, re.S,
    )
    assert m, "_extract_topics_with_ai body not found"
    return m.group(1)


def test_batch_attempt_and_failure_counters_exist():
    body = _extract_topics_body()
    assert "batches_attempted = 0" in body
    assert "batches_failed = 0" in body
    assert "batches_attempted += 1" in body
    assert "batches_failed += 1" in body


def test_total_batch_failure_raises_instead_of_returning_empty_success():
    body = _extract_topics_body()
    assert "batches_attempted > 0 and batches_failed == batches_attempted" in body
    assert "raise RuntimeError(" in body


def test_config_delay_headings_ternary_fixed():
    m = re.search(
        r"DELAY_RAG_HEADINGS_FOR_LOAD_TEST = os\.getenv\(\s*'DELAY_RAG_HEADINGS_FOR_LOAD_TEST',\s*(.*?)\n\s*\)",
        CONFIG_SRC, re.S,
    )
    assert m, "DELAY_RAG_HEADINGS_FOR_LOAD_TEST assignment not found"
    ternary = m.group(1)
    assert "'true' if" in ternary, (
        "both ternary branches previously evaluated to 'false' - the documented "
        "load-test delay behavior could never activate"
    )
    assert "else 'false'" in ternary


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
    print(f"All {len(tests)} tests passed - bug #21 fix is in place.")
    sys.exit(0)
