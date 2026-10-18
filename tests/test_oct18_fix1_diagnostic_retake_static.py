"""Fix 1 (18 Oct): the student diagnostic result screen no longer offers
"Retake with new questions". Static checks only (no browser available)."""
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LMS_STUDENT = ROOT / "static" / "lms" / "lms-student.js"
STUDENT_DIAG = ROOT / "static" / "student" / "js" / "student-diagnostic.js"
LMS_ROUTES = ROOT / "app" / "routes" / "lms_routes.py"
STUDENT_BASE = ROOT / "templates" / "student" / "base.html"


def _read(p):
    return p.read_text(encoding="utf-8")


def _render_diag_results_src():
    src = _read(LMS_STUDENT)
    start = src.index("function renderDiagnosticResults(")
    end = src.index("/* Learning path step actions */", start)
    return src[start:end]


def _strip_line_comments(js):
    # Drop `// ...` comments that start a line (the fix leaves an explanatory comment that
    # quotes the old label); string literals that would be rendered are kept.
    return "\n".join(l for l in js.splitlines() if not l.lstrip().startswith("//"))


def test_retake_with_new_questions_label_is_not_rendered_anywhere_in_lms_student_js():
    code = _strip_line_comments(_read(LMS_STUDENT))
    assert "Retake with new questions" not in code
    assert "'Retake with new questions" not in _read(LMS_STUDENT)


def test_render_diagnostic_results_does_not_call_retake_in_either_path():
    body = _render_diag_results_src()
    # Neither the inline (sd-btn) nor the modal (lms-btn) path wires a retake button.
    code = _strip_line_comments(body)
    assert "lmsRetakeDiagnostic" not in code
    assert "Retake" not in code


def test_render_diagnostic_results_keeps_other_actions_in_both_paths():
    body = _render_diag_results_src()
    assert body.count("Start Learning Chat") == 2, "inline + modal path"
    assert body.count(">Continue</button>") == 2, "inline + modal path"
    assert body.count("lmsOpenWeakAreaChats()") == 2
    assert body.count("Start challenge") == 2


def test_lms_retake_diagnostic_function_still_defined():
    assert re.search(r"window\.lmsRetakeDiagnostic\s*=\s*async\s+function", _read(LMS_STUDENT))


def test_backend_retake_route_unchanged():
    assert 'retake=bool(body.get("retake"))' in _read(LMS_ROUTES)


def test_student_diagnostic_hub_request_retake_button_untouched():
    src = _read(STUDENT_DIAG)
    assert "function retakeButtonHtml()" in src
    assert "Request a retake" in src
    assert "sdRetakeDiagnostic()" in src


def test_student_base_cache_bust_bumped_for_lms_student_js():
    m = re.search(r"lms/lms-student\.js'\) \}\}\?v=(\w+)", _read(STUDENT_BASE))
    assert m, "lms-student.js must carry a ?v= cache-buster"
    assert m.group(1) != "20261016a", "cache-buster must be bumped from the pre-fix value"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_lms_student_js_parses():
    r = subprocess.run(["node", "--check", str(LMS_STUDENT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
