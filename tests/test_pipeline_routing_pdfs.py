"""Route-level tests for run_pdf_quiz_pipeline driven by REAL PDF bytes.

The earlier routing tests mocked the canonical extractor, which is the same
gap that let the "Generate Quiz from notes fails" bug reach production.
Here the PDFs are generated with reportlab and go through the real document
parser; only the DB, LLM generation and display-review steps are mocked.

Covers both flows:
    (a) notes PDF -> Generate Quiz succeeds via content generation
    (b) Q-prefixed diagnostic paper -> printed numbers and answer key kept
"""
from __future__ import annotations

import io
import os
import unittest
from unittest import mock

os.environ.setdefault("SKIP_EXTRA_STARTUP", "true")
os.environ.setdefault("SKIP_DB_INIT", "true")
os.environ.setdefault("ENV", "local")


def _make_pdf(lines: list[str]) -> bytes:
    """One text line per drawString, new page when the current one fills up."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    y = height - 50
    for line in lines:
        if y < 50:
            c.showPage()
            y = height - 50
        c.setFont("Helvetica", 11)
        c.drawString(50, y, line)
        y -= 16
    c.save()
    return buf.getvalue()


# A Q-prefixed paper in the DIL SAATHI layout: "Q<n>." stems, a metadata
# banner under each stem, A-D options, then a mapping-table answer key.
_KEY = {1: "C", 2: "B", 3: "A", 4: "D", 5: "A", 6: "B",
        7: "C", 8: "D", 9: "B", 10: "C", 11: "B"}


def _q_prefixed_paper_lines() -> list[str]:
    lines = ["Baseline Diagnostic Assessment - Mathematics Grade IX", ""]
    for n in range(1, 12):
        lines.append(f"Q{n}. Which statement about item number {n} is correct?")
        lines.append(
            f"Domain: Numbers | Topic: Topic {n} | Cognitive Level: Application"
        )
        for label in "ABCD":
            lines.append(f"{label}. Choice {label.lower()} for question {n}")
    lines.append("Answer Key and Assessment Mapping")
    lines.append("Q Answer Domain Topic Cognitive Level")
    for n, letter in _KEY.items():
        lines.append(f"{n} {letter} Numbers Topic {n} Application")
    return lines


_NOTES_LINES = [
    "Chapter 3: Quadratic Equations",
    "A quadratic equation has the general form ax^2 + bx + c = 0, a not zero.",
    "The discriminant b^2 - 4ac tells us how many real roots there are.",
    "1. If the discriminant is positive there are two real roots.",
    "2. If it is zero there is one repeated root.",
    "3. If it is negative there are no real roots.",
    "Answers to the practice problems are discussed in class.",
]


def _mcq(stem: str):
    from app.services.quiz.models import MCQOption, MCQQuestion

    return MCQQuestion(
        question_text=stem,
        options=[
            MCQOption(label=lab, text=f"{stem} option {lab.lower()}")
            for lab in "ABCD"
        ],
        correct_option_label="A",
    )


class RealPdfRoutingTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("QUIZ_DOCUMENT_PARSER", None)
        from app.services.quiz.document_parser import parser as parser_mod

        parser_mod._INSTANCES.clear()
        self.saved: list = []
        self._next_id = 0
        self.assessment = mock.MagicMock()
        self.assessment.title = "Grade IX Maths"
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
            mock.patch("app.services.lms.diagnostic_timer_service.estimate_question_times"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, lines, *, question_count=None):
        from app.services.quiz.pipeline import run_pdf_quiz_pipeline

        return run_pdf_quiz_pipeline(
            assessment_id=1,
            rag_thread_id="t",
            user_id=1,
            # In production this text comes from RAG ingest of the same PDF.
            pdf_text="\n".join(lines),
            pdf_bytes=_make_pdf(lines),
            question_count=question_count,
        )

    # (a) ------------------------------------------------------------------
    def test_notes_pdf_generate_quiz_succeeds(self):
        """Notes PDF with a numbered list and the word 'Answers' - the case
        most likely to be mis-detected as an exam paper. It must still
        produce a quiz via content generation instead of failing."""
        self.assessment.assessment_type = "quiz"
        with mock.patch(
            "app.services.quiz.pipeline._generate_mcqs_from_any_pdf",
            return_value=[_mcq("Discriminant"), _mcq("Roots")],
        ) as gen, mock.patch(
            "app.services.quiz.pipeline.extract_qa_from_text",
            side_effect=RuntimeError("no Q&A in notes"),
        ):
            result = self._run(_NOTES_LINES, question_count=2)
        gen.assert_called()
        self.assertEqual(result["question_count"], 2)
        self.assertEqual(result["mode"], "pdf_ai")

    # (b) ------------------------------------------------------------------
    def test_q_prefixed_diagnostic_keeps_numbers_and_answers(self):
        self.assessment.assessment_type = "diagnostic"
        with mock.patch(
            "app.services.quiz.pipeline.extract_qa_from_text",
            side_effect=AssertionError("diagnostic must not use the LLM Q&A path"),
        ), mock.patch(
            "app.services.quiz.pipeline._generate_mcqs_from_any_pdf",
            side_effect=AssertionError("diagnostic must not generate content MCQs"),
        ):
            result = self._run(_q_prefixed_paper_lines())

        self.assertEqual(result["question_count"], 11)
        numbers = [s["source_question_number"] for s in self.saved]
        self.assertEqual(numbers, list(range(1, 12)))

        for saved in self.saved:
            n = saved["source_question_number"]
            opts = saved["options"]
            # A-D kept in paper order (preserve_option_order), so the
            # answer-key letter still points at the right option.
            self.assertEqual([o["label"] for o in opts], ["A", "B", "C", "D"])
            correct = opts[saved["correct_option_index"]]["label"]
            self.assertEqual(correct, _KEY[n], f"Q{n}")
            self.assertEqual(saved["correct_answer_raw"], _KEY[n])
            # Metadata banner must not leak into the stem.
            self.assertNotIn("Domain:", saved["question_text"])
            self.assertIn(f"item number {n}", saved["question_text"])


if __name__ == "__main__":
    unittest.main()
