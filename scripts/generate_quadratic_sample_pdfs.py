#!/usr/bin/env python3
"""Generate Grade 8 Math Diagnostic Q&A + Target content PDFs (topic-wise)."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pymupdf

OUT_DIR = os.path.join(ROOT, "sample_pdfs", "grade8_quadratic_equations")

MARGIN_LEFT = 56
MARGIN_TOP = 56
LINE_HEIGHT = 13
FONT_SIZE = 10
PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MAX_Y = PAGE_HEIGHT - 50


def _new_writer() -> tuple[pymupdf.Document, pymupdf.Page, float]:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    return doc, page, float(MARGIN_TOP)


def _write_line(doc: pymupdf.Document, page: pymupdf.Page, y: float, text: str) -> tuple[pymupdf.Page, float]:
    if y > MAX_Y:
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        y = float(MARGIN_TOP)
    page.insert_text((MARGIN_LEFT, y), text, fontsize=FONT_SIZE, fontname="helv")
    return page, y + LINE_HEIGHT


def _write_lines(doc: pymupdf.Document, page: pymupdf.Page, y: float, lines: list[str]) -> tuple[pymupdf.Page, float]:
    for line in lines:
        page, y = _write_line(doc, page, y, line)
    return page, y


def _save_doc(doc: pymupdf.Document, path: str) -> int:
    total_chars = sum(len(p.get_text()) for p in doc)
    doc.save(path)
    doc.close()
    return total_chars


def build_diagnostic_qa_pdf(path: str) -> int:
    doc, page, y = _new_writer()

    lines = [
        "Grade 8 Mathematics - Deep Diagnostic Assessment",
        "Topics: Fractions | Algebra | Geometry",
        "Class: 8th Grade | Total: 18 Multiple Choice Questions",
        "",
        "INSTRUCTIONS: Circle the best answer (A, B, C, or D) for each question.",
        "",
        "=" * 60,
        "SECTION A: FRACTIONS (Questions 1-6)",
        "=" * 60,
        "",
        "1. Simplify: 12/18",
        "   A) 1/3",
        "   B) 2/3",
        "   C) 3/4",
        "   D) 4/6",
        "",
        "2. Which fraction is equivalent to 0.75?",
        "   A) 1/4",
        "   B) 2/5",
        "   C) 3/4",
        "   D) 4/5",
        "",
        "3. Add: 1/4 + 2/3",
        "   A) 3/7",
        "   B) 3/12",
        "   C) 11/12",
        "   D) 5/6",
        "",
        "4. Multiply: 2/5 x 3/4",
        "   A) 5/9",
        "   B) 6/20",
        "   C) 3/10",
        "   D) 1/2",
        "",
        "5. Divide: 3/4 / 1/2",
        "   A) 3/8",
        "   B) 1/2",
        "   C) 3/2",
        "   D) 2/3",
        "",
        "6. A recipe uses 2/3 cup sugar. You make half the recipe. How much sugar?",
        "   A) 1/6 cup",
        "   B) 1/3 cup",
        "   C) 1/2 cup",
        "   D) 2/3 cup",
        "",
        "=" * 60,
        "SECTION B: ALGEBRA (Questions 7-12)",
        "=" * 60,
        "",
        "7. Solve for x: 3x + 7 = 22",
        "   A) x = 3",
        "   B) x = 5",
        "   C) x = 7",
        "   D) x = 15",
        "",
        "8. Which is a quadratic equation?",
        "   A) 2x + 1 = 9",
        "   B) x^2 - 5 = 0",
        "   C) 1/x = 4",
        "   D) x^3 + 2 = 0",
        "",
        "9. Solve: x^2 - 9 = 0",
        "   A) x = 3 only",
        "   B) x = -3 only",
        "   C) x = 3 or x = -3",
        "   D) x = 9 or x = -9",
        "",
        "10. Factor: x^2 + 7x + 12",
        "    A) (x + 2)(x + 6)",
        "    B) (x + 3)(x + 4)",
        "    C) (x + 1)(x + 12)",
        "    D) (x - 3)(x - 4)",
        "",
        "11. Solve: 2(x - 4) = 10",
        "    A) x = 5",
        "    B) x = 7",
        "    C) x = 9",
        "    D) x = 11",
        "",
        "12. What is the discriminant of x^2 + 6x + 9 = 0?",
        "    A) -36",
        "    B) 0",
        "    C) 9",
        "    D) 36",
        "",
    ]
    page, y = _write_lines(doc, page, y, lines)

    geometry = [
        "=" * 60,
        "SECTION C: GEOMETRY (Questions 13-18)",
        "=" * 60,
        "",
        "13. The sum of angles in a triangle is:",
        "    A) 90 degrees",
        "    B) 180 degrees",
        "    C) 270 degrees",
        "    D) 360 degrees",
        "",
        "14. A rectangle has length 12 cm and width 5 cm. Its area is:",
        "    A) 17 cm^2",
        "    B) 34 cm^2",
        "    C) 60 cm^2",
        "    D) 120 cm^2",
        "",
        "15. The circumference of a circle with radius 7 cm is (use pi = 22/7):",
        "    A) 22 cm",
        "    B) 44 cm",
        "    C) 49 cm",
        "    D) 154 cm",
        "",
        "16. Two angles are supplementary. One is 65 degrees. The other is:",
        "    A) 25 degrees",
        "    B) 65 degrees",
        "    C) 115 degrees",
        "    D) 125 degrees",
        "",
        "17. A triangle has base 10 cm and height 6 cm. Area =",
        "    A) 16 cm^2",
        "    B) 30 cm^2",
        "    C) 60 cm^2",
        "    D) 120 cm^2",
        "",
        "18. A square has perimeter 36 cm. Side length =",
        "    A) 6 cm",
        "    B) 9 cm",
        "    C) 12 cm",
        "    D) 18 cm",
        "",
        "=" * 60,
        "ANSWER KEY",
        "=" * 60,
        "",
        "FRACTIONS",
        "1. B   12/18 = 2/3",
        "2. C   0.75 = 3/4",
        "3. C   1/4 + 2/3 = 3/12 + 8/12 = 11/12",
        "4. C   2/5 x 3/4 = 6/20 = 3/10",
        "5. C   3/4 / 1/2 = 3/4 x 2/1 = 3/2",
        "6. B   Half of 2/3 = 1/3 cup",
        "",
        "ALGEBRA",
        "7. B   3x = 15, x = 5",
        "8. B   x^2 - 5 = 0 is degree 2",
        "9. C   x^2 = 9, x = 3 or -3",
        "10. B  x^2 + 7x + 12 = (x + 3)(x + 4)",
        "11. C  2(x - 4) = 10, x - 4 = 5, x = 9",
        "12. B  D = 36 - 36 = 0",
        "",
        "GEOMETRY",
        "13. B  Triangle angle sum = 180 degrees",
        "14. C  Area = 12 x 5 = 60 cm^2",
        "15. B  C = 2 pi r = 2 x 22/7 x 7 = 44 cm",
        "16. C  180 - 65 = 115 degrees",
        "17. B  Area = 1/2 x 10 x 6 = 30 cm^2",
        "18. B  36 / 4 = 9 cm",
        "",
        "END OF DIAGNOSTIC",
    ]
    page, y = _write_lines(doc, page, y, geometry)
    return _save_doc(doc, path)


def build_target_content_pdf(path: str) -> int:
    doc, page, y = _new_writer()

    blocks = [
        [
            "Grade 8 Mathematics - Target Content PDF",
            "Topics: Fractions, Algebra, Geometry",
            "For Learning Chat and weak-area remediation",
            "",
        ],
        [
            "PART 1: FRACTIONS",
            "",
            "A fraction represents part of a whole: a/b where b is not 0.",
            "",
            "Simplifying: divide numerator and denominator by their GCF.",
            "Example: 12/18 = (12/6)/(18/6) = 2/3",
            "",
            "Adding with unlike denominators: find LCD first.",
            "Example: 1/4 + 2/3 = 3/12 + 8/12 = 11/12",
            "",
            "Multiplying: multiply numerators and denominators.",
            "Example: 2/5 x 3/4 = 6/20 = 3/10",
            "",
            "Dividing: multiply by the reciprocal.",
            "Example: 3/4 / 1/2 = 3/4 x 2/1 = 3/2",
            "",
            "Word problem tip: 'half of 2/3' means 1/2 x 2/3 = 1/3.",
            "",
        ],
        [
            "PART 2: ALGEBRA",
            "",
            "Linear equations: isolate the variable using inverse operations.",
            "Example: 3x + 7 = 22",
            "Step 1: 3x = 15",
            "Step 2: x = 5",
            "",
            "Quadratic equations have degree 2: ax^2 + bx + c = 0.",
            "",
            "Square root method: x^2 - 9 = 0",
            "x^2 = 9, so x = 3 or x = -3",
            "",
            "Factoring: x^2 + 7x + 12 = (x + 3)(x + 4)",
            "Set each factor to zero to find roots.",
            "",
            "Distributive property: 2(x - 4) = 10",
            "2x - 8 = 10, 2x = 18, x = 9",
            "",
            "Discriminant D = b^2 - 4ac tells number of real roots.",
            "For x^2 + 6x + 9 = 0: D = 36 - 36 = 0 (one repeated root).",
            "",
        ],
        [
            "PART 3: GEOMETRY",
            "",
            "Triangle angle sum: interior angles add to 180 degrees.",
            "",
            "Rectangle area = length x width.",
            "Example: 12 cm x 5 cm = 60 cm^2",
            "",
            "Circle circumference C = 2 pi r.",
            "Example: r = 7, pi = 22/7, C = 44 cm",
            "",
            "Supplementary angles sum to 180 degrees.",
            "If one angle is 65, the other is 115 degrees.",
            "",
            "Triangle area = 1/2 x base x height.",
            "Example: base 10, height 6, area = 30 cm^2",
            "",
            "Square perimeter = 4 x side.",
            "If perimeter is 36 cm, side = 9 cm.",
            "",
            "Study tip: draw a diagram for every geometry word problem.",
            "",
            "END OF TARGET CONTENT",
        ],
    ]

    for block in blocks:
        page, y = _write_lines(doc, page, y, block)

    return _save_doc(doc, path)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    diag_path = os.path.join(OUT_DIR, "diagnostic_qa_quadratic_equations_grade8.pdf")
    target_path = os.path.join(OUT_DIR, "target_content_quadratic_equations_grade8.pdf")

    diag_chars = build_diagnostic_qa_pdf(diag_path)
    target_chars = build_target_content_pdf(target_path)

    print("Created:")
    print(f"  Diagnostic Q&A: {diag_path}")
    print(f"    - 6 Fractions + 6 Algebra + 6 Geometry = 18 questions")
    print(f"    - Extracted text: {diag_chars} chars")
    print(f"  Target content: {target_path}")
    print(f"    - Extracted text: {target_chars} chars")

    if diag_chars < 500:
        raise SystemExit("ERROR: Diagnostic PDF generation failed.")
    if target_chars < 500:
        raise SystemExit("ERROR: Target PDF generation failed.")


if __name__ == "__main__":
    main()
