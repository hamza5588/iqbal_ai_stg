"""
Tests for app/utils/chat_lock.py.

QA-sweep Critical bug: chat responses could get delivered to the wrong request under
concurrent load. Root cause: the "one turn at a time" guard (`_user_chat_locks` in
rag_routes.py) was a plain in-process `dict` of `threading.Lock()`. Gunicorn runs multiple
WORKER PROCESSES (see CLAUDE.md), each with independent memory, so that lock only ever
serialized requests landing on the same worker - two requests for the same thread on two
different workers ran the LangGraph turn concurrently against the same checkpointed thread
state, and could deliver one request's answer to a different, unrelated request (confirmed
live).

Fix: a Redis-backed cross-process lock (same URL-resolution convention as
app/utils/chat_progress.py), with an in-process threading.Lock() fallback if Redis is
unavailable (never worse than the pre-fix behavior). These tests exercise the module directly
- CI/local test environments generally don't have Redis running, so most of these exercise the
fallback path, which is exactly the degrade-gracefully path that must itself stay correct.
"""
import inspect
import threading
import time

import pytest

chat_lock = pytest.importorskip("app.utils.chat_lock")


class TestAcquireReleaseBasics:
    def test_acquire_then_release_then_reacquire_succeeds(self):
        key = "test_key_basic"
        handle = chat_lock.acquire_chat_lock(key, timeout_seconds=2)
        assert handle is not None
        chat_lock.release_chat_lock(handle)

        handle2 = chat_lock.acquire_chat_lock(key, timeout_seconds=2)
        assert handle2 is not None
        chat_lock.release_chat_lock(handle2)

    def test_second_acquire_on_held_key_times_out(self):
        key = "test_key_contended"
        handle = chat_lock.acquire_chat_lock(key, timeout_seconds=5)
        assert handle is not None
        try:
            start = time.monotonic()
            handle2 = chat_lock.acquire_chat_lock(key, timeout_seconds=1)
            elapsed = time.monotonic() - start
            assert handle2 is None
            assert elapsed >= 0.9  # actually waited, didn't just fail instantly
        finally:
            chat_lock.release_chat_lock(handle)

    def test_different_keys_do_not_contend(self):
        h1 = chat_lock.acquire_chat_lock("test_key_a", timeout_seconds=2)
        h2 = chat_lock.acquire_chat_lock("test_key_b", timeout_seconds=2)
        assert h1 is not None
        assert h2 is not None
        chat_lock.release_chat_lock(h1)
        chat_lock.release_chat_lock(h2)

    def test_release_none_does_not_raise(self):
        chat_lock.release_chat_lock(None)  # must be a no-op, never raise

    def test_double_release_does_not_raise(self):
        key = "test_key_double_release"
        handle = chat_lock.acquire_chat_lock(key, timeout_seconds=2)
        chat_lock.release_chat_lock(handle)
        chat_lock.release_chat_lock(handle)  # second release must not raise


class TestConcurrencyWithinProcess:
    """Even the in-process fallback path must correctly serialize same-process threads -
    this is the one case that worked correctly even before this fix, and must keep working."""

    def test_two_threads_never_hold_the_same_key_simultaneously(self):
        key = "test_key_thread_race"
        holder = {"owner": None}
        violations = []

        def worker(name):
            handle = chat_lock.acquire_chat_lock(key, timeout_seconds=5)
            if handle is None:
                return
            try:
                if holder["owner"] is not None:
                    violations.append((name, holder["owner"]))
                holder["owner"] = name
                time.sleep(0.05)
                if holder["owner"] != name:
                    violations.append((name, holder["owner"]))
            finally:
                holder["owner"] = None
                chat_lock.release_chat_lock(handle)

        threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert violations == [], f"Two threads held the same lock key simultaneously: {violations}"


class TestRoutesWiredToChatLock:
    """Static wiring checks: the old in-process-only lock mechanism must actually be gone from
    both call sites the QA sweep flagged, and both must use the new cross-process module."""

    def test_rag_routes_imports_chat_lock(self):
        import app.routes.rag_routes as rag_routes
        src = inspect.getsource(rag_routes)
        assert "from app.utils.chat_lock import acquire_chat_lock, release_chat_lock" in src
        assert "acquire_chat_lock(thread_id, lock_wait_seconds)" in src
        assert "release_chat_lock(chat_lock_handle)" in src
        # The old in-process-only mechanism must be gone, not just unused.
        assert "_get_user_chat_lock" not in src
        assert "_user_chat_locks" not in src

    def test_lesson_routes_imports_chat_lock(self):
        import app.routes.lesson_routes as lesson_routes
        src = inspect.getsource(lesson_routes)
        assert "from app.utils.chat_lock import acquire_chat_lock, release_chat_lock" in src
        assert "acquire_chat_lock(f\"lesson_qa_user_{user_id}\", lock_wait_seconds)" in src
        assert "release_chat_lock(lesson_qa_lock_handle)" in src
        assert "_get_lesson_qa_user_lock" not in src
        assert "_lesson_qa_user_locks" not in src
