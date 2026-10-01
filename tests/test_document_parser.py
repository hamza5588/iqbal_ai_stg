"""Tests for the new document_parser layer.

The old regex-only approach is being replaced by a canonical-document +
deterministic-MCQ-parser pipeline (see report.txt for context). These tests
prove the new layer:

    1. Assembles MCQs from CanonicalBlocks *by printed question number*,
       not by list index (spec Step 5-6, Step 19).
    2. Joins the answer key by that same printed number, so a reordered
       question stream still produces correct answers (spec Step 6).
    3. Preserves A-D option order.
    4. Strips per-question metadata banners.
    5. Reports duplicates / missing-key / gap situations via the validator
       instead of silently continuing (spec Step 10).
    6. Uses the ``native`` parser as a safe default in the registry.

No PDF is opened here - the tests feed synthetic CanonicalDocument objects
so they run in milliseconds and do not depend on paddleocr.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("SKIP_EXTRA_STARTUP", "true")
os.environ.setdefault("SKIP_DB_INIT", "true")
os.environ.setdefault("ENV", "local")


def _blk(page, order, text, *, latex=None, block_type=None, metadata=False):
    """Small factory that keeps the fixtures readable."""
    from app.services.quiz.document_parser.canonical import BlockType, CanonicalBlock

    bt = block_type or (BlockType.EQUATION if (latex and not text) else BlockType.TEXT)
    blk = CanonicalBlock(
        block_id=f"p{page}-b{order}",
        page_number=page,
        block_type=bt,
        text=text,
        latex=latex,
        reading_order=order,
    )
    if metadata:
        blk.extra["is_metadata_banner"] = True
    return blk


def _doc(*blocks):
    from app.services.quiz.document_parser.canonical import CanonicalDocument, CanonicalPage

    by_page = {}
    for blk in blocks:
        by_page.setdefault(blk.page_number, []).append(blk)
    pages = [
        CanonicalPage(page_number=pn, blocks=blks)
        for pn, blks in sorted(by_page.items())
    ]
    return CanonicalDocument(document_id="test", parser="test", pages=pages)


class MCQFromCanonicalTests(unittest.TestCase):
    def test_q_prefixed_paper_preserves_source_number(self):
        """Q11 in the PDF must land as source_question_number=11, not 3."""
        from app.services.quiz.document_parser.mcq_from_canonical import parse_mcqs

        doc = _doc(
            _blk(1, 0, "Q1. Which of the following is an irrational number?"),
            _blk(1, 1, "Domain: Numbers | Topic: Classification", metadata=True),
            _blk(1, 2, "A. 5/8"),
            _blk(1, 3, "B. 0.75"),
            _blk(1, 4, "C. sqrt(7)"),
            _blk(1, 5, "D. -4"),
            _blk(2, 0, "Q8. Which is the correct factorization?"),
            _blk(2, 1, "A. (x+3)(x+2)"),
            _blk(2, 2, "B. (x-3)(x+2)"),
            _blk(2, 3, "C. (x-6)(x+1)"),
            _blk(2, 4, "D. (x+6)(x-1)"),
            _blk(3, 0, "Q11. Find the value of 'a' if log_a 8 = 3/2."),
            _blk(3, 1, "A. 2"),
            _blk(3, 2, "B. 4"),
            _blk(3, 3, "C. 6"),
            _blk(3, 4, "D. 8"),
            _blk(3, 5, "Answer Key and Assessment Mapping"),
            _blk(3, 6, "1 C Numbers Classification"),
            _blk(3, 7, "8 D Algebra Factorization"),
            _blk(3, 8, "11 B Numbers Logarithms"),
        )
        result = parse_mcqs(doc)
        nums = [m.source_question_number for m in result.mcqs]
        self.assertEqual(nums, [1, 8, 11])
        # A-D preserved
        for m in result.mcqs:
            self.assertEqual([o.label for o in m.options], ["A", "B", "C", "D"])
        # Metadata banner does NOT leak into the stem.
        self.assertNotIn("Domain:", result.mcqs[0].stem)
        # Source page traceable per question.
        self.assertEqual(result.mcqs[0].source_page, 1)
        self.assertEqual(result.mcqs[2].source_page, 3)

    def test_answer_key_joined_by_source_number_not_index(self):
        """If MCQs arrive in order Q10, Q8, Q9, the key must still produce
        Q8->D, Q9->A, Q10->C - never key[0] blindly assigned to Q10.

        This is the exact class of bug the spec (Step 6) forbids."""
        from app.services.quiz.document_parser.mcq_from_canonical import (
            apply_answer_key,
            parse_mcqs,
        )

        doc = _doc(
            _blk(1, 0, "Q10. tenth question stem"),
            _blk(1, 1, "A. a"), _blk(1, 2, "B. b"),
            _blk(1, 3, "C. c"), _blk(1, 4, "D. d"),
            _blk(1, 5, "Q8. eighth question stem"),
            _blk(1, 6, "A. a"), _blk(1, 7, "B. b"),
            _blk(1, 8, "C. c"), _blk(1, 9, "D. d"),
            _blk(1, 10, "Q9. ninth question stem"),
            _blk(1, 11, "A. a"), _blk(1, 12, "B. b"),
            _blk(1, 13, "C. c"), _blk(1, 14, "D. d"),
            _blk(2, 0, "Answer Key"),
            _blk(2, 1, "8 D"), _blk(2, 2, "9 A"), _blk(2, 3, "10 C"),
        )
        result = parse_mcqs(doc)
        mapping = apply_answer_key(result.mcqs, result.answer_key)
        self.assertEqual(mapping, {8: "D", 9: "A", 10: "C"})

    def test_bare_number_paper_still_works(self):
        """The Q-prefix support must not break plain '1.', '2.' papers."""
        from app.services.quiz.document_parser.mcq_from_canonical import parse_mcqs

        doc = _doc(
            _blk(1, 0, "1. The number 5.181818... is"),
            _blk(1, 1, "A. terminating"),
            _blk(1, 2, "B. repeating"),
            _blk(1, 3, "C. non-repeating"),
            _blk(1, 4, "D. irrational"),
        )
        result = parse_mcqs(doc)
        self.assertEqual([m.source_question_number for m in result.mcqs], [1])
        self.assertEqual(result.mcqs[0].options[1].text, "repeating")

    def test_table_layout_answer_key(self):
        """Answer keys often split '1' and 'C' into adjacent blocks in a table."""
        from app.services.quiz.document_parser.mcq_from_canonical import parse_mcqs

        doc = _doc(
            _blk(1, 0, "Q1. stem one"),
            _blk(1, 1, "A. a"), _blk(1, 2, "B. b"),
            _blk(1, 3, "C. c"), _blk(1, 4, "D. d"),
            _blk(2, 0, "Answer Key"),
            _blk(2, 1, "1"),
            _blk(2, 2, "C"),
        )
        result = parse_mcqs(doc)
        self.assertEqual(result.answer_key[0].question_number, 1)
        self.assertEqual(result.answer_key[0].letter, "C")


class ValidatorTests(unittest.TestCase):
    def test_missing_answer_key_is_fatal(self):
        from app.services.quiz.document_parser.mcq_from_canonical import (
            MCQCandidate,
            MCQExtractionResult,
            MCQOption,
        )
        from app.services.quiz.document_parser.validator import validate_extraction

        mcq = MCQCandidate(
            source_question_number=1, printed_label="Q1",
            stem="stem", stem_latex=None,
            options=[MCQOption(label=lab, text=lab) for lab in "ABCD"],
            source_page=1,
        )
        result = MCQExtractionResult(mcqs=[mcq], answer_key=[])
        report = validate_extraction(result, require_answer_key=True)
        self.assertFalse(report.ok)
        codes = [i.code for i in report.errors]
        self.assertIn("missing_answer_key", codes)

    def test_duplicate_number_is_fatal(self):
        from app.services.quiz.document_parser.mcq_from_canonical import (
            AnswerKeyEntry, MCQCandidate, MCQExtractionResult, MCQOption,
        )
        from app.services.quiz.document_parser.validator import validate_extraction

        opts = [MCQOption(label=lab, text=lab) for lab in "ABCD"]
        mcqs = [
            MCQCandidate(source_question_number=1, printed_label="Q1",
                         stem="s", stem_latex=None, options=opts, source_page=1),
            MCQCandidate(source_question_number=1, printed_label="Q1",
                         stem="s", stem_latex=None, options=opts, source_page=1),
        ]
        key = [AnswerKeyEntry(question_number=1, letter="A")]
        report = validate_extraction(
            MCQExtractionResult(mcqs=mcqs, answer_key=key),
            require_answer_key=True,
        )
        self.assertFalse(report.ok)
        self.assertIn("duplicate_question_number", [i.code for i in report.errors])

    def test_numbering_gap_is_warning_not_error(self):
        from app.services.quiz.document_parser.mcq_from_canonical import (
            AnswerKeyEntry, MCQCandidate, MCQExtractionResult, MCQOption,
        )
        from app.services.quiz.document_parser.validator import validate_extraction

        opts = [MCQOption(label=lab, text=lab) for lab in "ABCD"]
        mcqs = [
            MCQCandidate(source_question_number=n, printed_label=f"Q{n}",
                         stem="s", stem_latex=None, options=opts, source_page=1)
            for n in (1, 2, 5)
        ]
        key = [AnswerKeyEntry(question_number=n, letter="A") for n in (1, 2, 5)]
        report = validate_extraction(
            MCQExtractionResult(mcqs=mcqs, answer_key=key),
            require_answer_key=True,
        )
        self.assertTrue(report.ok)  # warning-only
        self.assertIn("numbering_gap", [i.code for i in report.warnings])


class RegistryTests(unittest.TestCase):
    def test_default_parser_is_native(self):
        from app.services.quiz.document_parser import get_document_parser

        # Reset any prior selection.
        for var in ("QUIZ_DOCUMENT_PARSER",):
            os.environ.pop(var, None)
        parser = get_document_parser()
        self.assertEqual(parser.name, "native")

    def test_unknown_parser_raises(self):
        from app.services.quiz.document_parser import (
            DocumentParserError,
            get_document_parser,
        )

        with self.assertRaises(DocumentParserError):
            get_document_parser("does-not-exist")


if __name__ == "__main__":
    unittest.main()
