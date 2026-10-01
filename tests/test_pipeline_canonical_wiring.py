"""Integration tests for the canonical-parser branch inside
:func:`run_pdf_quiz_pipeline`.

These tests verify the WIRING - not the underlying document parser - so
they use a stub :class:`DocumentParser` that returns a hand-built
:class:`CanonicalDocument`. That means the tests are fast, deterministic
and prove the pipeline no longer writes ``idx + 1`` as
``source_question_number`` when the canonical path owns the run.
"""
from __future__ import annotations

import os
import unittest
from typing import List
from unittest import mock

os.environ.setdefault("SKIP_EXTRA_STARTUP", "true")
os.environ.setdefault("SKIP_DB_INIT", "true")
os.environ.setdefault("ENV", "local")


def _make_stub_document():
    from app.services.quiz.document_parser.canonical import (
        BlockType,
        CanonicalBlock,
        CanonicalDocument,
        CanonicalPage,
    )

    def _blk(page, order, text):
        return CanonicalBlock(
            block_id=f"p{page}-b{order}",
            page_number=page,
            block_type=BlockType.TEXT,
            text=text,
            reading_order=order,
        )

    blocks_p1 = [
        _blk(1, 0, "Q1. First stem."),
        _blk(1, 1, "A. one"),
        _blk(1, 2, "B. two"),
        _blk(1, 3, "C. three"),
        _blk(1, 4, "D. four"),
        _blk(1, 5, "Q8. Eighth stem."),
        _blk(1, 6, "A. one"),
        _blk(1, 7, "B. two"),
        _blk(1, 8, "C. three"),
        _blk(1, 9, "D. four"),
        _blk(1, 10, "Q11. Eleventh stem."),
        _blk(1, 11, "A. one"),
        _blk(1, 12, "B. two"),
        _blk(1, 13, "C. three"),
        _blk(1, 14, "D. four"),
        _blk(1, 15, "Answer Key"),
        _blk(1, 16, "1 C"),
        _blk(1, 17, "8 D"),
        _blk(1, 18, "11 B"),
    ]
    return CanonicalDocument(
        document_id="stub",
        parser="stub",
        parser_version="1.0",
        pages=[CanonicalPage(page_number=1, blocks=blocks_p1)],
    )


class _StubParser:
    """DocumentParser lookalike that returns a fixed CanonicalDocument."""

    name = "stub"
    version = "1.0"

    def __init__(self, doc):
        self._doc = doc

    def parse(self, pdf_bytes, *, document_id):
        return self._doc

    def assess_quality(self, pdf_bytes):
        from app.services.quiz.document_parser.parser import ParserQualityReport

        return ParserQualityReport(acceptable=True, reason="stub")


class CanonicalWiringTests(unittest.TestCase):
    def setUp(self):
        os.environ["QUIZ_DOCUMENT_PARSER"] = "stub"
        # Register the stub in the parser registry.
        from app.services.quiz.document_parser import parser as parser_mod

        self._doc = _make_stub_document()
        parser_mod._INSTANCES.pop("stub", None)
        parser_mod.register_parser("stub", lambda: _StubParser(self._doc))

    def tearDown(self):
        os.environ.pop("QUIZ_DOCUMENT_PARSER", None)
        from app.services.quiz.document_parser import parser as parser_mod

        parser_mod._INSTANCES.pop("stub", None)

    def test_pipeline_writes_printed_source_numbers_not_idx_plus_one(self):
        """The whole point of Phase 2: Q1/Q8/Q11 must be persisted with
        source_question_number = 1, 8, 11 - never 1, 2, 3."""
        from app.services.quiz.pipeline import _run_canonical_extraction

        with mock.patch(
            "app.services.quiz.document_parser.to_mcq_question.apply_answer_key"
        ) as _:
            pass  # not needed here; kept as an example hook

        batch, source_numbers, letters, warnings, conf, name = _run_canonical_extraction(
            pdf_bytes=b"%PDF-1.4 stub",  # bytes are ignored by the stub parser
            assessment_id=999,
            is_diagnostic=True,
            wanted=None,
            progress_callback=None,
        )
        self.assertEqual(source_numbers, [1, 8, 11])
        self.assertEqual(letters, ["C", "D", "B"])
        self.assertEqual(len(batch), 3)
        self.assertEqual(name, "stub")
        # Each MCQ carries the answer letter from the key on the PRINTED
        # number, not from list position.
        self.assertEqual(batch[0].correct_option_label, "C")
        self.assertEqual(batch[1].correct_option_label, "D")
        self.assertEqual(batch[2].correct_option_label, "B")

    def test_env_selects_canonical_or_legacy_path(self):
        """Phase 3: canonical path is the DEFAULT; only
        ``QUIZ_DOCUMENT_PARSER=legacy`` returns to the old extractor.

        This is deliberately the opposite of Phase 2's opt-in behavior -
        the whole point of Phase 3 is to make document-intelligence the
        primary path so the old regex-only recovery can be removed."""
        from app.services.quiz.pipeline import _canonical_parser_enabled

        os.environ.pop("QUIZ_DOCUMENT_PARSER", None)
        self.assertTrue(_canonical_parser_enabled())  # default = canonical

        os.environ["QUIZ_DOCUMENT_PARSER"] = "native"
        self.assertTrue(_canonical_parser_enabled())

        os.environ["QUIZ_DOCUMENT_PARSER"] = "paddle"
        self.assertTrue(_canonical_parser_enabled())

        os.environ["QUIZ_DOCUMENT_PARSER"] = "legacy"
        self.assertFalse(_canonical_parser_enabled())  # explicit opt-out

        os.environ.pop("QUIZ_DOCUMENT_PARSER", None)

    def test_validator_failure_raises_and_does_not_fall_through(self):
        """A fatal validator failure must raise AssessmentDocValidationError -
        never silently downgrade to the legacy path (spec Step 10).

        Per-question option-shape errors are DEMOTED to warnings so a
        single bad question does not kill the whole upload; genuine
        document-wide failures (no answer key at all, duplicate numbers)
        still raise. We test the latter here."""
        from app.services.quiz.assessment_doc_validation import AssessmentDocValidationError
        from app.services.quiz.document_parser.canonical import (
            BlockType, CanonicalBlock, CanonicalDocument, CanonicalPage,
        )
        from app.services.quiz.document_parser import parser as parser_mod
        from app.services.quiz.pipeline import _run_canonical_extraction

        # Build a well-formed doc but with NO answer-key section at all.
        # For a diagnostic, that is a fatal error (missing_answer_key).
        blocks = [
            CanonicalBlock(block_id="p1-b0", page_number=1,
                           block_type=BlockType.TEXT,
                           text="Q1. Good stem", reading_order=0),
            CanonicalBlock(block_id="p1-b1", page_number=1,
                           block_type=BlockType.TEXT,
                           text="A. one", reading_order=1),
            CanonicalBlock(block_id="p1-b2", page_number=1,
                           block_type=BlockType.TEXT,
                           text="B. two", reading_order=2),
            CanonicalBlock(block_id="p1-b3", page_number=1,
                           block_type=BlockType.TEXT,
                           text="C. three", reading_order=3),
            CanonicalBlock(block_id="p1-b4", page_number=1,
                           block_type=BlockType.TEXT,
                           text="D. four", reading_order=4),
        ]
        bad_doc = CanonicalDocument(
            document_id="bad", parser="stub",
            pages=[CanonicalPage(page_number=1, blocks=blocks)],
        )
        parser_mod._INSTANCES.pop("stub", None)
        parser_mod.register_parser("stub", lambda: _StubParser(bad_doc))

        with self.assertRaises(AssessmentDocValidationError) as ctx:
            _run_canonical_extraction(
                pdf_bytes=b"%PDF-1.4",
                assessment_id=1,
                is_diagnostic=True,
                wanted=None,
                progress_callback=None,
            )
        self.assertIn("PDF extraction validation failed", str(ctx.exception.detail))

    def test_per_question_option_errors_are_skipped_not_fatal(self):
        """A single question with the wrong option count must NOT kill
        the whole upload - that one question is dropped and the rest
        save normally."""
        from app.services.quiz.document_parser.canonical import (
            BlockType, CanonicalBlock, CanonicalDocument, CanonicalPage,
        )
        from app.services.quiz.document_parser import parser as parser_mod
        from app.services.quiz.pipeline import _run_canonical_extraction

        def _b(order, text):
            return CanonicalBlock(
                block_id=f"p1-b{order}", page_number=1,
                block_type=BlockType.TEXT, text=text, reading_order=order,
            )

        # Q1-Q4 are well-formed; Q2 has only 3 options (parser glitch).
        blocks = [
            _b(0, "Q1. first stem"), _b(1, "A. apple"), _b(2, "B. banana"),
            _b(3, "C. cherry"), _b(4, "D. date"),
            _b(5, "Q2. broken stem"), _b(6, "A. apple"),
            _b(7, "B. banana"), _b(8, "C. cherry"),  # <- missing D
            _b(9, "Q3. third stem"), _b(10, "A. apple"), _b(11, "B. banana"),
            _b(12, "C. cherry"), _b(13, "D. date"),
            _b(14, "Q4. fourth stem"), _b(15, "A. apple"), _b(16, "B. banana"),
            _b(17, "C. cherry"), _b(18, "D. date"),
            _b(19, "Answer Key"),
            _b(20, "1 A"), _b(21, "2 B"), _b(22, "3 C"), _b(23, "4 D"),
        ]
        doc = CanonicalDocument(
            document_id="partial", parser="stub",
            pages=[CanonicalPage(page_number=1, blocks=blocks)],
        )
        parser_mod._INSTANCES.pop("stub", None)
        parser_mod.register_parser("stub", lambda: _StubParser(doc))

        batch, source_numbers, letters, warnings, conf, name = _run_canonical_extraction(
            pdf_bytes=b"%PDF",
            assessment_id=1,
            is_diagnostic=True,
            wanted=None,
            progress_callback=None,
        )
        # Q2 dropped, Q1/Q3/Q4 saved - printed numbers preserved.
        self.assertEqual(source_numbers, [1, 3, 4])
        self.assertEqual(letters, ["A", "C", "D"])
        # Warnings must mention the dropped question so ops can debug.
        joined = " ".join(warnings)
        self.assertIn("Question 2", joined)
        self.assertIn("Skipped 1 questions", joined)


def _mcq(stem: str, correct: str = "A"):
    from app.services.quiz.models import MCQOption, MCQQuestion

    return MCQQuestion(
        question_text=stem,
        options=[
            MCQOption(label=lab, text=f"{stem} option {lab.lower()}")
            for lab in "ABCD"
        ],
        correct_option_label=correct,
    )


_NOTES_TEXT = (
    "Chapter 3 Quadratic Equations. A quadratic equation has the general form "
    "ax^2 + bx + c = 0 where a is not zero. The discriminant decides how many "
    "real roots the equation has."
)
_QA_TEXT = (
    "1. What is 2 + 2?\nA. 3\nB. 4\nC. 5\nD. 6\n"
    "2. What is 3 + 3?\nA. 5\nB. 6\nC. 7\nD. 8\n"
    "Answer Key\n1 B\n2 B\n"
)


class PipelineRoutingTests(unittest.TestCase):
    """Route-level tests for run_pdf_quiz_pipeline (DB / LLM mocked).

    Regression: after Phase 3 the canonical path ran on EVERY upload and
    re-raised its validation error, so a lesson-notes PDF in "Generate
    Quiz" failed instead of falling back to content-generated MCQs."""

    def setUp(self):
        os.environ.pop("QUIZ_DOCUMENT_PARSER", None)
        self.saved: list = []
        self._next_id = 100
        self.assessment = mock.MagicMock()
        self.assessment.title = "Algebra Quiz"
        self.assessment.description = None

        def _create_question(**kwargs):
            self._next_id += 1
            self.saved.append(kwargs)
            return mock.MagicMock(id=self._next_id)

        patches = [
            mock.patch("app.services.quiz.pipeline.assessment_service.get_assessment",
                       return_value=self.assessment),
            mock.patch("app.services.quiz.pipeline.assessment_service.link_pdf_source",
                       return_value=mock.MagicMock(id=1)),
            mock.patch("app.services.quiz.pipeline.assessment_service.add_questions"),
            mock.patch("app.services.quiz.pipeline.assessment_service.save_pdf_extraction"),
            mock.patch("app.services.quiz.pipeline._set_pdf_source_status"),
            mock.patch("app.services.quiz.pipeline.get_db"),
            mock.patch("app.services.quiz.pipeline.question_bank_service.create_question",
                       side_effect=_create_question),
            mock.patch("app.services.quiz.display_format_qa.review_mcqs_for_display",
                       side_effect=lambda qs: qs),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, text, *, question_count=None):
        from app.services.quiz.pipeline import run_pdf_quiz_pipeline

        return run_pdf_quiz_pipeline(
            assessment_id=1,
            rag_thread_id="t",
            user_id=1,
            pdf_text=text,
            pdf_bytes=b"%PDF-1.4",
            question_count=question_count,
        )

    def test_quiz_from_notes_pdf_skips_canonical_and_generates(self):
        self.assessment.assessment_type = "quiz"
        with mock.patch(
            "app.services.quiz.pipeline._run_canonical_extraction",
            side_effect=AssertionError("canonical must not run on a notes PDF"),
        ), mock.patch(
            "app.services.quiz.pipeline._generate_mcqs_from_any_pdf",
            return_value=[_mcq("Roots"), _mcq("Discriminant")],
        ):
            result = self._run(_NOTES_TEXT, question_count=2)
        self.assertEqual(result["mode"], "pdf_ai")
        self.assertEqual(result["question_count"], 2)

    def test_quiz_falls_back_when_canonical_rejects_pdf(self):
        from app.services.quiz.assessment_doc_validation import AssessmentDocValidationError

        self.assessment.assessment_type = "quiz"
        with mock.patch(
            "app.services.quiz.pipeline._run_canonical_extraction",
            side_effect=AssessmentDocValidationError(detail="no answer key rows"),
        ), mock.patch(
            "app.services.quiz.pipeline.extract_qa_from_text",
            side_effect=RuntimeError("llm unavailable"),
        ), mock.patch(
            "app.services.quiz.pipeline._generate_mcqs_from_any_pdf",
            return_value=[_mcq("Sum"), _mcq("Double")],
        ):
            result = self._run(_QA_TEXT, question_count=2)
        self.assertEqual(result["question_count"], 2)
        self.assertTrue(
            any("Layout-aware Q&A extraction skipped" in w for w in result["warnings"])
        )

    def test_diagnostic_still_fails_loud(self):
        from app.services.quiz.assessment_doc_validation import AssessmentDocValidationError

        self.assessment.assessment_type = "diagnostic"
        with mock.patch(
            "app.services.quiz.pipeline._run_canonical_extraction",
            side_effect=AssessmentDocValidationError(detail="missing answer key"),
        ), mock.patch(
            "app.services.quiz.pipeline.extract_qa_from_text",
            side_effect=AssertionError("diagnostic must not fall back to legacy"),
        ):
            with self.assertRaises(AssessmentDocValidationError):
                self._run(_QA_TEXT)
        self.assertEqual(self.saved, [])

    def test_quiz_top_up_after_canonical_keeps_printed_numbers(self):
        """Canonical Q&A + content top-up: PDF questions keep their printed
        numbers; generated ones get None, never an index that can collide."""
        self.assessment.assessment_type = "quiz"
        canonical = (
            [_mcq("Q4 stem", "B"), _mcq("Q9 stem", "C")],
            [4, 9],
            ["B", "C"],
            ["Parser: stub"],
            0.9,
            "stub",
        )
        with mock.patch(
            "app.services.quiz.pipeline._run_canonical_extraction",
            return_value=canonical,
        ), mock.patch(
            "app.services.quiz.pipeline.extract_qa_from_text",
            side_effect=AssertionError("canonical Q&A must not be re-extracted"),
        ), mock.patch(
            "app.services.quiz.pipeline._generate_mcqs_from_any_pdf",
            return_value=[_mcq("Extra")],
        ):
            result = self._run(_QA_TEXT, question_count=3)
        self.assertEqual(result["question_count"], 3)
        self.assertEqual(result["mode"], "mixed")
        self.assertEqual(
            [s["source_question_number"] for s in self.saved], [4, 9, None]
        )


if __name__ == "__main__":
    unittest.main()
