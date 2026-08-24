"""
Heading-extraction tier policy (embedded outline vs TOC probe vs body scan).

PDF-backed cases read IQBAL_TEST_PDF_DIR (typically tests/fixtures/pdfs/). When that
env var is unset, those cases skip. Do not commit the 88 MB / 44 MB customer PDFs.
Heavy files are marked @pytest.mark.slow and excluded from the default run.
"""
import os
from pathlib import Path

import pytest

rag_service = pytest.importorskip("app.utils.rag_service")

from langchain_core.documents import Document  # noqa: E402


def _pdf_dir():
    raw = os.environ.get("IQBAL_TEST_PDF_DIR")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def _enough_page_docs(n=20, chars=400):
    return [
        Document(page_content=("Section body text. " * 20)[:chars], metadata={"page": i + 1})
        for i in range(n)
    ]


def test_outline_gap_thresholds():
    # Validated: 0.04 accept, 0.69 reject.
    dense = list(range(1, 26))
    assert rag_service._outline_gap(dense, 25) <= 0.25
    assert abs(rag_service._outline_gap(dense, 25) - 0.04) < 0.02
    sparse = [1, 16, 48]
    gap = rag_service._outline_gap(sparse, 48)
    assert gap > 0.25
    assert abs(gap - 0.69) < 0.05


def test_low_text_skip_is_zero_llm_calls(monkeypatch):
    docs = [Document(page_content="x" * 40, metadata={"page": i + 1}) for i in range(8)]
    monkeypatch.setattr(rag_service, "_find_uploaded_pdf_for_thread", lambda tid: None)
    result = rag_service._extract_topics_with_ai(docs, user_id=1, thread_id="user_1_abc")
    assert result["heading_tier"] == "skip_low_text"
    assert result["llm_calls"] == 0
    assert result["topics"] == []


def test_heading_policy_skips_pdf_table_when_env_unset():
    if _pdf_dir() is not None:
        pytest.skip("IQBAL_TEST_PDF_DIR is set; PDF table tests run separately")
    assert os.environ.get("IQBAL_TEST_PDF_DIR") in (None, "")


def _require_pdf(name):
    pdf_dir = _pdf_dir()
    if pdf_dir is None:
        pytest.skip("IQBAL_TEST_PDF_DIR unset")
    path = pdf_dir / name
    if not path.is_file():
        pytest.skip(f"fixture not present: {name}")
    return path


def _run_extract_on_pdf(monkeypatch, pdf_path, page_count=20):
    monkeypatch.setattr(rag_service, "_find_uploaded_pdf_for_thread", lambda tid: pdf_path)
    return rag_service._extract_topics_with_ai(
        _enough_page_docs(page_count), user_id=1, thread_id="user_1_abc"
    )


def test_alpha_meditec_is_t1_outline(monkeypatch):
    path = _require_pdf("Alpha meditec-92.5mb.pdf")
    result = _run_extract_on_pdf(monkeypatch, path)
    assert result["heading_tier"] == "T1"
    assert result["llm_calls"] == 0
    assert result["topics_count"] >= 500


def test_versa_manual_is_t1_outline(monkeypatch):
    path = _require_pdf("Versa Manual-14.6mb.pdf")
    result = _run_extract_on_pdf(monkeypatch, path)
    assert result["heading_tier"] == "T1"
    assert result["llm_calls"] == 0
    assert result["topics_count"] >= 250


def test_cie_physics_is_t2_or_t3_with_bounded_calls(monkeypatch):
    path = _require_pdf("CIE PHYSICS Index-1 Measurement Units-2 Forces Motion.pdf")
    result = _run_extract_on_pdf(monkeypatch, path, page_count=47)
    assert result["heading_tier"] in ("T2", "T2_T3", "T3")
    assert result["llm_calls"] <= 20


def test_sacred_theory_offset_zero_budget(monkeypatch):
    path = _require_pdf("The sacred theory of the Earth _ Project Gutenberg-7.1mb.pdf")
    result = _run_extract_on_pdf(monkeypatch, path, page_count=78)
    assert result["heading_tier"] in ("T2", "T2_T3", "T3")
    assert result["llm_calls"] <= 78


def test_beauty_and_the_beast_rejects_sparse_outline(monkeypatch):
    path = _require_pdf("Beautu-and-the-beast-pdf-2.1mb.pdf")
    topics, gap, n = rag_service._read_embedded_outline(str(path))
    if len(topics) >= 5:
        assert gap > 0.25
    result = _run_extract_on_pdf(monkeypatch, path, page_count=20)
    assert result["heading_tier"] in ("T2", "T2_T3", "T3", "skip_flat_prose")
    assert result["llm_calls"] <= 20


def test_cern_accelerator_budget(monkeypatch):
    path = _require_pdf("CERN Accelerator Magnets-4.2mb.pdf")
    result = _run_extract_on_pdf(monkeypatch, path, page_count=31)
    assert result["heading_tier"] in ("T2", "T2_T3", "T3")
    assert result["llm_calls"] <= 31


def test_wizard_of_oz_t3_budget(monkeypatch):
    path = _require_pdf("Scanned&Readable-wonderful-wizard-of-oz-17.4mb.pdf")
    result = _run_extract_on_pdf(monkeypatch, path, page_count=108)
    assert result["heading_tier"] in ("T3", "T2_T3")
    assert result["llm_calls"] <= 108


def test_text_huge_pages_skip_flat_prose(monkeypatch):
    path = _require_pdf("Text&HugePages-TOWERING-TRIUMPH-FROM-A-MAJOR-6.1mb.pdf")
    result = _run_extract_on_pdf(monkeypatch, path, page_count=12)
    assert result["heading_tier"] == "skip_flat_prose"
    assert result["llm_calls"] <= 1


def test_flow12_skip_low_text(monkeypatch):
    path = _require_pdf("Flow 12 chatbot va.pdf")
    # Low-text skip uses page_docs stats, not the PDF path.
    docs = [Document(page_content="x" * 64, metadata={"page": i + 1}) for i in range(12)]
    monkeypatch.setattr(rag_service, "_find_uploaded_pdf_for_thread", lambda tid: path)
    result = rag_service._extract_topics_with_ai(docs, user_id=1, thread_id="user_1_abc")
    assert result["heading_tier"] == "skip_low_text"
    assert result["llm_calls"] == 0


@pytest.mark.slow
def test_heavy_3414_page_file_is_slow_and_capped(monkeypatch):
    path = _require_pdf("synthetic-4000-page.pdf")
    if not path.is_file():
        # Alternate names used in the corpus zip.
        pdf_dir = _pdf_dir()
        candidates = list(pdf_dir.glob("*3414*")) + list(pdf_dir.glob("*4000*"))
        if not candidates:
            pytest.skip("heavy PDF fixture not present")
        path = candidates[0]
    sampled = rag_service._sample_pages_for_body_scan(_enough_page_docs(3414, chars=200))
    assert len(sampled) <= rag_service._HEADING_BODY_SCAN_PAGE_CAP + 1
