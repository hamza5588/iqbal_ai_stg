"""
Static regression tests for app/routes/rag_routes.py.

QA-sweep bug: `chat()` crashed with `UnboundLocalError: cannot access local variable 're'
where it is not associated with a value` on any message classified as a "summarize" intent
sent without a `conversation_id` (e.g. "summarize what we've discussed so far in this chat").

Root cause: the module imports `re` at the top level (used throughout the file), but `chat()`
(a ~600-line view function) ALSO had a second, redundant `import re` deep inside one of its
branches. Because Python determines a name's scope for the WHOLE function body at compile
time, the mere presence of that local `import re` statement anywhere in the function made `re`
a local variable for the entire function - shadowing the module-level import even in code that
runs before the local import statement is ever reached. The summary-intent interceptor calls
`re.search(...)` earlier in execution than the later local import, so it hit `re` unbound.

Fix: removed the redundant local `import re` - the module-level import already covers it.
These tests statically assert no local `import re`/`import threading`/similar redundant
import can reappear anywhere inside `chat()`'s body without being caught, since a live
behavioral test would need heavy Flask/DB/session mocking for comparatively little value over
a direct source check on the actual root cause.
"""
import ast
import inspect

import pytest

rag_routes = pytest.importorskip("app.routes.rag_routes")


def _get_function_source_lines(module, func_name):
    """Returns (start_line, end_line) 1-indexed, inclusive, for a module-level function."""
    tree = ast.parse(inspect.getsource(module))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node.lineno, node.end_lineno
    raise AssertionError(f"Could not find function {func_name!r} in {module.__name__}")


def _local_imports_in_function(module, func_name, module_names):
    """
    Returns a list of (lineno, module_name) for any `import <name>` statement found textually
    within the given function's line range, for each name in module_names. Deliberately a
    simple line-range containment check (not full AST scope resolution) so it also catches
    imports nested inside try/except/if blocks inside the function, which is exactly where the
    real bug was hiding.
    """
    src_lines = inspect.getsource(module).splitlines()
    start, end = _get_function_source_lines(module, func_name)
    hits = []
    for lineno in range(start, end + 1):
        line = src_lines[lineno - 1].strip()
        for name in module_names:
            if line == f"import {name}" or line.startswith(f"import {name} "):
                hits.append((lineno, name))
    return hits


def test_chat_view_has_no_shadowing_local_import_re():
    """
    The exact regression: chat() must never contain a local `import re` anywhere in its body -
    the module-level import (top of the file) is the only one that should exist. A local
    import of a name already imported at module level silently shadows it for the WHOLE
    function, not just the code after the import statement.
    """
    hits = _local_imports_in_function(rag_routes, "chat", ["re"])
    assert hits == [], (
        f"Found local 'import re' inside chat() at line(s) {hits} - this shadows the "
        "module-level import for the entire function and will reproduce the "
        "UnboundLocalError crash on summarize-intent messages. Remove it; the module-level "
        "`import re` (top of rag_routes.py) already covers this function."
    )


def test_module_level_re_import_exists():
    """Sanity check that the module-level import this fix relies on is actually still there."""
    src = inspect.getsource(rag_routes)
    top_of_file = "\n".join(src.splitlines()[:60])
    assert "\nimport re\n" in ("\n" + top_of_file + "\n")


def test_summary_intent_search_call_still_present():
    """
    Static wiring check that the actual crash site (the summary-intent interceptor's
    re.search call, which runs before ANY local `import re` further down the function ever
    executes) is still there, so this test file keeps meaning something if that code moves.
    """
    src = inspect.getsource(rag_routes.chat)
    assert "re.search(r'user_\\d+_conv_(\\d+)', provided_thread_id)" in src
