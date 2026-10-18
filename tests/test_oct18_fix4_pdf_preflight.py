"""Fix 4 (18 Oct): shared PDF preflight (app/utils/pdf_preflight.py) rejects scanned,
encrypted, empty, blank and corrupted PDFs before any AI / ingest work, for quiz,
lesson and RAG uploads. PDFs are generated in-test with PyMuPDF; routes run on a bare
Flask app with services mocked (no DB, no LLM)."""
import os
import tempfile

if "iqbalai_local.db" in os.environ.get("DATABASE_URL", "iqbalai_local.db"):
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "oct18_fix4.db").replace("\\", "/")

import io
import pathlib
import time
import zlib
from types import SimpleNamespace

import pytest
from flask import Flask

pymupdf = pytest.importorskip("pymupdf")

from app.utils import pdf_preflight as pp  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLES = sorted((ROOT / "sample_pdfs").rglob("*.pdf"))

TEXT = "Quadratic equations are polynomial equations of degree two in one variable. " * 15
MCQ_TEXT = "\n".join(
    f"Q{i}. What is the value of x if x^2 - {i*i} = 0?\n"
    f"A) {i}   B) {i+1}   C) {i+2}   D) {i+3}\nAnswer: A\n"
    for i in range(1, 11)
)


@pytest.fixture(autouse=True)
def _never_the_dev_db():
    from app.config import Config

    if "iqbalai_local.db" in str(getattr(Config, "SQLALCHEMY_DATABASE_URI", "")):
        pytest.skip("app configured against the dev DB; refusing to run")


# ------------------------------------------------------------- PDF factories

def text_pdf(text=TEXT, pages=1):
    d = pymupdf.open()
    for _ in range(pages):
        p = d.new_page()
        p.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=11)
    return d.tobytes()


def _png():
    src = pymupdf.open(stream=text_pdf(), filetype="pdf")
    return src[0].get_pixmap(dpi=50).tobytes("png")


def scanned_pdf(pages=3, page_numbers=False):
    d = pymupdf.open()
    png = _png()
    for i in range(pages):
        p = d.new_page()
        p.insert_image(p.rect, stream=png)
        if page_numbers:
            p.insert_text((290, 820), str(i + 1), fontsize=9)
    return d.tobytes()


def mixed_pdf():
    d = pymupdf.open()
    p = d.new_page()
    p.insert_textbox(pymupdf.Rect(50, 50, 550, 300), TEXT, fontsize=11)
    p.insert_image(pymupdf.Rect(50, 400, 550, 800), stream=_png())
    return d.tobytes()


def text_doc_with_one_scanned_page():
    d = pymupdf.open(stream=text_pdf(pages=4), filetype="pdf")
    p = d.new_page()
    p.insert_image(p.rect, stream=_png())
    return d.tobytes()


def blank_pdf():
    d = pymupdf.open()
    d.new_page()
    return d.tobytes()


def encrypted_pdf():
    d = pymupdf.open(stream=text_pdf(), filetype="pdf")
    return d.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")


ZERO_PAGE_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
)


def bomb_pdf():
    payload = zlib.compress(b"\0" * (61 * 1024 * 1024), 9)
    return (
        b"%PDF-1.4\n1 0 obj<</Length " + str(len(payload)).encode() + b"/Filter/FlateDecode>>stream\n"
        + payload + b"\nendstream\nendobj\ntrailer<<>>\n%%EOF"
    )


# ------------------------------------------------------------- preflight unit tests

def test_valid_text_pdf_passes():
    r = pp.preflight_check_pdf(text_pdf())
    assert r == {"ok": True, "has_images": False}


def test_valid_mcq_format_text_pdf_passes():
    assert pp.preflight_check_pdf(text_pdf(MCQ_TEXT))["ok"] is True


def test_mixed_text_and_image_pdf_passes():
    r = pp.preflight_check_pdf(mixed_pdf())
    assert r["ok"] is True and r["has_images"] is True


def test_text_document_with_one_scanned_page_passes():
    assert pp.preflight_check_pdf(text_doc_with_one_scanned_page())["ok"] is True


def test_scanned_image_only_pdf_rejected():
    r = pp.preflight_check_pdf(scanned_pdf())
    assert r["ok"] is False and r["code"] == "PDF_SCANNED"
    assert r["message"] == pp.PDF_SCANNED_MESSAGE


def test_scanned_pdf_with_stray_page_numbers_still_rejected():
    r = pp.preflight_check_pdf(scanned_pdf(pages=4, page_numbers=True))
    assert r["ok"] is False and r["code"] == "PDF_SCANNED"


def test_blank_page_pdf_is_no_text():
    r = pp.preflight_check_pdf(blank_pdf())
    assert r["ok"] is False and r["code"] == "PDF_NO_TEXT"
    assert r["message"] == pp.PDF_NO_TEXT_MESSAGE


def test_encrypted_pdf_rejected():
    r = pp.preflight_check_pdf(encrypted_pdf())
    assert r["ok"] is False and r["code"] == "PDF_PASSWORD_PROTECTED"


def test_zero_page_pdf_is_empty():
    r = pp.preflight_check_pdf(ZERO_PAGE_PDF)
    assert r["ok"] is False and r["code"] == "PDF_EMPTY"


@pytest.mark.parametrize("cut", [40, 0.5])
def test_truncated_pdf_rejected(cut):
    good = text_pdf()
    n = cut if isinstance(cut, int) else int(len(good) * cut)
    r = pp.preflight_check_pdf(good[:n])
    assert r["ok"] is False and r["code"] in {"PDF_UNREADABLE", "PDF_NO_TEXT", "PDF_EMPTY"}


@pytest.mark.parametrize(
    "data",
    [b"%PDF-1.4\n" + b"\x00garbage" * 50, b"PK\x03\x04 this is a zip renamed to .pdf" * 10, b"hello world"],
)
def test_corrupt_or_non_pdf_bytes_unreadable(data):
    r = pp.preflight_check_pdf(data)
    assert r["ok"] is False and r["code"] == "PDF_UNREADABLE"
    assert r["message"] == pp.PDF_UNREADABLE_MESSAGE


def test_decompression_bomb_rejected_as_too_complex():
    r = pp.preflight_check_pdf(bomb_pdf())
    assert r["ok"] is False and r["code"] == "PDF_TOO_COMPLEX"


def test_validation_timeout(monkeypatch):
    monkeypatch.setattr(pp, "preflight_check_pdf_inner", lambda *a, **k: time.sleep(2) or {"ok": True})
    t0 = time.time()
    r = pp.preflight_check_pdf(text_pdf(), timeout_seconds=0.2)
    assert time.time() - t0 < 1.5
    assert r["ok"] is False and r["code"] == "PDF_VALIDATION_TIMEOUT"


@pytest.mark.skipif(not SAMPLES, reason="no sample_pdfs present")
@pytest.mark.parametrize("path", SAMPLES, ids=[p.name for p in SAMPLES])
def test_real_sample_pdfs_pass(path):
    r = pp.preflight_check_pdf(path.read_bytes())
    assert r["ok"] is True, r


def test_rag_routes_keeps_backward_compatible_aliases():
    from app.routes import rag_routes

    assert rag_routes._preflight_check_pdf is pp.preflight_check_pdf
    assert rag_routes._preflight_check_pdf_inner is pp.preflight_check_pdf_inner
    assert rag_routes._check_decompression_bomb is pp.check_decompression_bomb


# ------------------------------------------------------------- assessment validation

from app.services.quiz.assessment_doc_validation import (  # noqa: E402
    ASSESSMENT_FORMAT_ERROR,
    AssessmentDocValidationError,
    assert_supported_assessment_file,
    public_error_message,
)


@pytest.mark.parametrize(
    "factory, reason, message",
    [
        (scanned_pdf, "PDF_SCANNED", pp.PDF_SCANNED_MESSAGE),
        (encrypted_pdf, "PDF_PASSWORD_PROTECTED", pp.PDF_PASSWORD_PROTECTED_MESSAGE),
        (blank_pdf, "PDF_NO_TEXT", pp.PDF_NO_TEXT_MESSAGE),
        (lambda: ZERO_PAGE_PDF, "PDF_EMPTY", pp.PDF_EMPTY_MESSAGE),
        (lambda: b"%PDF-1.4\n" + b"\x00garbage" * 50, "PDF_UNREADABLE", pp.PDF_UNREADABLE_MESSAGE),
    ],
)
def test_assert_supported_assessment_file_raises_specific_reason(factory, reason, message):
    with pytest.raises(AssessmentDocValidationError) as ei:
        assert_supported_assessment_file("quiz.pdf", factory(), label="Assessment PDF")
    e = ei.value
    assert e.reason == reason
    assert e.public_message == message and str(e) == message
    assert public_error_message(e) == message
    assert reason in (e.detail or "")


def test_assert_supported_assessment_file_accepts_valid_and_mcq_pdfs():
    assert_supported_assessment_file("quiz.pdf", text_pdf(MCQ_TEXT))
    assert_supported_assessment_file("quiz.pdf", mixed_pdf())


@pytest.mark.parametrize(
    "filename, data",
    [
        ("quiz.pdf", b""),                                # empty
        ("quiz.docx", b"%PDF-1.4 whatever"),              # wrong extension
        ("quiz.pdf", b"PK\x03\x04 not a pdf" * 20),       # renamed non-PDF: magic check
    ],
)
def test_structure_level_errors_still_canonical_format_error(filename, data):
    with pytest.raises(AssessmentDocValidationError) as ei:
        assert_supported_assessment_file(filename, data)
    e = ei.value
    assert str(e) == ASSESSMENT_FORMAT_ERROR
    assert e.reason == "INVALID_FORMAT"
    assert public_error_message(e) == ASSESSMENT_FORMAT_ERROR


def test_pipeline_style_detail_only_errors_keep_canonical_message():
    e = AssessmentDocValidationError(detail="MCQ conversion failed — no questions were saved.")
    assert str(e) == ASSESSMENT_FORMAT_ERROR and public_error_message(e) == ASSESSMENT_FORMAT_ERROR
    assert e.detail.startswith("MCQ conversion failed")


def test_public_error_message_for_other_exceptions_unchanged():
    assert public_error_message(ValueError("boom")) == "boom"
    assert public_error_message(ValueError("")) == ASSESSMENT_FORMAT_ERROR


# ------------------------------------------------------------- API bypass (quiz routes)

@pytest.fixture
def lms_client(monkeypatch):
    from app.routes import lms_routes

    calls = SimpleNamespace(create=[], enqueue=[])
    monkeypatch.setattr("app.utils.auth.sync_session_user", lambda: True)
    monkeypatch.setattr(
        lms_routes.assessment_service, "create_assessment",
        lambda **kw: calls.create.append(kw) or SimpleNamespace(id=77),
    )
    monkeypatch.setattr(lms_routes, "enqueue_or_run_pdf_quiz", lambda **kw: calls.enqueue.append(kw) or {"async": False})
    monkeypatch.setattr(lms_routes, "_require_assessment_owner", lambda qid: (None, SimpleNamespace(id=qid)))
    monkeypatch.setattr(lms_routes, "_discard_empty_draft_quiz", lambda aid: None, raising=False)

    app = Flask(__name__)
    app.secret_key = "t"
    app.config["USE_CELERY_FOR_INGESTION"] = False
    app.register_blueprint(lms_routes.bp, url_prefix="/api/lms")
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 10
        s["role"] = "teacher"
    return c, calls


def _post_quiz(c, data_bytes, filename="quiz.pdf"):
    return c.post(
        "/api/lms/quizzes/from-pdf",
        data={"title": "Q", "time_limit_minutes": "10", "file": (io.BytesIO(data_bytes), filename)},
        content_type="multipart/form-data",
    )


def test_api_from_pdf_scanned_rejected_before_any_work(lms_client):
    c, calls = lms_client
    r = _post_quiz(c, scanned_pdf())
    assert r.status_code == 400
    err = r.get_json()["error"]
    assert err["code"] == "validation_error"
    assert err["message"] == pp.PDF_SCANNED_MESSAGE
    assert err["details"] == {"reason": "PDF_SCANNED"}
    assert calls.create == [] and calls.enqueue == [], "no assessment / AI job for a scanned PDF"


@pytest.mark.parametrize(
    "factory, reason",
    [(encrypted_pdf, "PDF_PASSWORD_PROTECTED"), (blank_pdf, "PDF_NO_TEXT"), (lambda: ZERO_PAGE_PDF, "PDF_EMPTY")],
)
def test_api_from_pdf_other_preflight_failures(lms_client, factory, reason):
    c, calls = lms_client
    r = _post_quiz(c, factory())
    assert r.status_code == 400
    assert r.get_json()["error"]["details"] == {"reason": reason}
    assert calls.create == [] and calls.enqueue == []


def test_api_from_pdf_renamed_non_pdf_gets_format_error(lms_client):
    c, calls = lms_client
    r = _post_quiz(c, b"PK\x03\x04 not a pdf" * 20, filename="quiz.pdf")
    assert r.status_code == 400
    err = r.get_json()["error"]
    assert err["message"] == ASSESSMENT_FORMAT_ERROR and err["details"] == {"reason": "INVALID_FORMAT"}
    assert calls.create == []


def test_api_from_pdf_valid_pdf_proceeds(lms_client):
    c, calls = lms_client
    r = _post_quiz(c, text_pdf(MCQ_TEXT))
    assert r.status_code == 200, r.get_json()
    assert len(calls.create) == 1 and len(calls.enqueue) == 1


def test_api_process_pdf_scanned_rejected(lms_client):
    c, calls = lms_client
    r = c.post(
        "/api/lms/quizzes/5/process-pdf",
        data={"file": (io.BytesIO(scanned_pdf()), "quiz.pdf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert r.get_json()["error"]["details"] == {"reason": "PDF_SCANNED"}
    assert calls.enqueue == []


# ------------------------------------------------------------- legacy create_lesson

@pytest.fixture
def lesson_client(monkeypatch):
    from app.routes import lesson_routes

    processed = []

    class FakeLessonService:
        def __init__(self, api_key=None):
            pass

        def process_file(self, file, details):
            processed.append(details)
            return {"error": "stop here"}

    monkeypatch.setattr(lesson_routes, "LessonService", FakeLessonService)
    monkeypatch.setattr(lesson_routes, "_get_lesson_api_key", lambda: "k")
    monkeypatch.setattr(lesson_routes.LessonModel, "check_title_exists", staticmethod(lambda uid, t: False))
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(lesson_routes.bp, url_prefix="/api/lessons")
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 10
        s["role"] = "teacher"
    return c, processed


def _post_lesson(c, data_bytes):
    return c.post(
        "/api/lessons/create_lesson",
        data={"lessonTitle": "L", "focusArea": "Math", "gradeLevel": "8", "file": (io.BytesIO(data_bytes), "lesson.pdf")},
        content_type="multipart/form-data",
    )


def test_create_lesson_scanned_pdf_rejected_before_processing(lesson_client):
    c, processed = lesson_client
    r = _post_lesson(c, scanned_pdf())
    assert r.status_code == 400
    assert r.get_json() == {"error": pp.PDF_SCANNED_MESSAGE, "code": "PDF_SCANNED"}
    assert processed == []


def test_create_lesson_text_pdf_reaches_processing(lesson_client):
    c, processed = lesson_client
    _post_lesson(c, text_pdf())
    assert len(processed) == 1


# ------------------------------------------------------------- reject_mostly_scanned (follow-up edit)

def test_mostly_scanned_with_page_numbers_default_rejected_but_assessment_mode_passes():
    data = scanned_pdf(pages=4, page_numbers=True)
    assert pp.preflight_check_pdf(data)["code"] == "PDF_SCANNED"  # lesson / RAG path (default)
    assert pp.preflight_check_pdf(data, reject_mostly_scanned=False)["ok"] is True
    assert_supported_assessment_file("quiz.pdf", data, label="Assessment PDF")  # does not raise


def test_fully_image_only_rejected_in_both_modes():
    data = scanned_pdf()
    assert pp.preflight_check_pdf(data)["code"] == "PDF_SCANNED"
    assert pp.preflight_check_pdf(data, reject_mostly_scanned=False)["code"] == "PDF_SCANNED"
    with pytest.raises(AssessmentDocValidationError) as ei:
        assert_supported_assessment_file("quiz.pdf", data, label="Assessment PDF")
    assert ei.value.reason == "PDF_SCANNED"


def test_non_assessment_label_prefixes_public_message():
    with pytest.raises(AssessmentDocValidationError) as ei:
        assert_supported_assessment_file("t.pdf", scanned_pdf(), label="Target content PDF")
    e = ei.value
    assert e.public_message == "Target content PDF: " + pp.PDF_SCANNED_MESSAGE
    assert public_error_message(e) == e.public_message and e.reason == "PDF_SCANNED"


def test_assessment_label_not_prefixed():
    with pytest.raises(AssessmentDocValidationError) as ei:
        assert_supported_assessment_file("q.pdf", encrypted_pdf(), label="Assessment PDF")
    assert ei.value.public_message == pp.PDF_PASSWORD_PROTECTED_MESSAGE


def test_create_lesson_mostly_scanned_still_rejected(lesson_client):
    c, processed = lesson_client
    r = _post_lesson(c, scanned_pdf(pages=4, page_numbers=True))
    assert r.status_code == 400 and r.get_json()["code"] == "PDF_SCANNED"
    assert processed == []


def test_rag_ingest_uses_default_strict_preflight():
    src = (ROOT / "app" / "routes" / "rag_routes.py").read_text(encoding="utf-8")
    assert "preflight = _preflight_check_pdf(file_bytes)" in src
