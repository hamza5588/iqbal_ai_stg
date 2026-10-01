"""Golden end-to-end test on the DIL SAATHI diagnostic PDF.

This test is the Phase 3 regression proof: the real 25-question DIL SAATHI
Baseline Diagnostic Mathematics IX PDF is passed through the CANONICAL
extraction stack (NativePyMuPDFParser -> parse_mcqs -> validator) and the
authoritative answer-key mapping is checked against the known values from
the PDF's own answer-key section.

If this test breaks, the extraction pipeline is silently drifting away
from the source PDF. That is the exact class of regression the pasted
spec (Step 18) requires us to guard against.

The test is skipped when the fixture PDF is not present, so it can live
in the repo without gating CI on a large binary."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("SKIP_EXTRA_STARTUP", "true")
os.environ.setdefault("SKIP_DB_INIT", "true")
os.environ.setdefault("ENV", "local")


_FIXTURE = Path(__file__).resolve().parents[1] / (
    "uploaded_files/"
    "user_1_lms_1790224003_a1b75b4e_Diagnostic Assessment DIL SAATHI.pdf"
)


@unittest.skipUnless(
    _FIXTURE.exists(),
    f"DIL SAATHI fixture PDF not present at {_FIXTURE}",
)
class DILSaathiGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app.services.quiz.document_parser import get_document_parser
        from app.services.quiz.document_parser.mcq_from_canonical import (
            apply_answer_key,
            parse_mcqs,
        )

        cls._get_document_parser = staticmethod(get_document_parser)
        cls._parse_mcqs = staticmethod(parse_mcqs)
        cls._apply_answer_key = staticmethod(apply_answer_key)
        cls._pdf_bytes = _FIXTURE.read_bytes()

    def _extract(self):
        parser = self._get_document_parser("native")
        doc = parser.parse(self._pdf_bytes, document_id="dil-saathi-golden")
        return self._parse_mcqs(doc)

    def test_all_25_questions_extracted_with_printed_numbers(self):
        """Every printed question 1..25 appears exactly once."""
        result = self._extract()
        nums = sorted(m.source_question_number for m in result.mcqs)
        self.assertEqual(nums, list(range(1, 26)))

    def test_answer_key_has_25_entries(self):
        """The answer-key section at the tail of the PDF is fully parsed."""
        result = self._extract()
        key_nums = {e.question_number for e in result.answer_key}
        self.assertEqual(key_nums, set(range(1, 26)))

    def test_golden_answer_mapping_matches_pdf(self):
        """The answer key is joined by PRINTED question number - the
        pipeline must reproduce the exact letters printed in the PDF's
        answer-key section.

        Values below come from the PDF's own answer key page; they are the
        contract this extraction path must preserve across refactors."""
        result = self._extract()
        mapping = self._apply_answer_key(result.mcqs, result.answer_key)
        expected = {
            1: "D",  2: "B",  3: "C",  4: "A",  5: "C",
            6: "D",  7: "D",  8: "A",  9: "C", 10: "B",
            11: "D", 12: "A", 13: "C", 14: "A", 15: "C",
            16: "D", 17: "C", 18: "A", 19: "B", 20: "D",
            21: "A", 22: "B", 23: "B", 24: "A", 25: "B",
        }
        for num, letter in expected.items():
            self.assertEqual(
                mapping.get(num),
                letter,
                f"Q{num}: expected {letter}, got {mapping.get(num)!r}",
            )

    def test_source_page_traceability(self):
        """Every extracted MCQ points at a real 1-based page number so
        production support can answer 'where did this question come from?'
        (spec Step 11)."""
        result = self._extract()
        for m in result.mcqs:
            self.assertGreaterEqual(m.source_page, 1)
            self.assertTrue(
                m.source_block_ids,
                f"Q{m.source_question_number} has no source block ids",
            )


if __name__ == "__main__":
    unittest.main()
