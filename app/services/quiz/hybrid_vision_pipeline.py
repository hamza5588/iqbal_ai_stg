# ============================================================
# MATH MCQ PDF EXTRACTOR (in-app)
# Dual-evidence hybrid pipeline (Qwen Vision + Groq)
#
# Used by quiz + diagnostic LMS flows via hybrid_mcq_extractor.
# Root persor.py is NOT imported by the app.
# ============================================================
#
# Architecture:
#
#   INPUT PDF
#       │
#       ▼
#   PDF Pre-check
#       │
#       ├──────────────────────┐
#       ▼                      ▼
#   PDF Text Extraction    PDF Page Render
#      (PyMuPDF)            (high-res image)
#       │                      │
#       ▼                      ▼
#   TEXT EVIDENCE          VISUAL EVIDENCE
#       │                      │
#       └──────────┬───────────┘
#                  ▼
#        Qwen Vision / Groq
#         Hybrid Analysis
#                  │
#                  ▼
#          Structured MCQs
#                  │
#                  ▼
#     Enrich (answer key + metadata)
#      + cross-page option repair
#                  │
#                  ▼
#              VALIDATOR
#                  │
#          ┌───────┴───────┐
#         PASS            FAIL
#          │               │
#          ▼               ▼
#      Final JSON    Higher-res retry
#
# Install:
#
#   pip install -U pymupdf pydantic python-dotenv \
#       langchain-core langchain-groq groq
#
# .env:
#
#   GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxx
#   GROQ_VISION_MODEL=qwen/qwen3.8-27b   # optional
#
# ============================================================

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pymupdf
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

# Resolved lazily so this module can be imported by the quiz pipeline
# without crashing the Flask app when GROQ_API_KEY is unset at import time.
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Groq vision model (Qwen multimodal). Override via .env or admin default.
VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")


def _require_groq_api_key() -> str:
    """Resolve Groq key at call time: env first, then admin SystemSettings."""
    key = (os.getenv("GROQ_API_KEY") or GROQ_API_KEY or "").strip()
    if key:
        return key
    try:
        from app.utils.llm_factory import _resolve_stored_api_key

        key = (_resolve_stored_api_key("groq") or "").strip()
    except Exception:
        key = ""
    if key:
        return key
    raise ValueError(
        "GROQ_API_KEY not found. Set GROQ_API_KEY in the container env "
        "or save a Groq API key in Admin → Settings."
    )


def resolve_vision_model() -> str:
    """Prefer env, then admin Groq default when it is a Qwen model, else built-in."""
    env_model = (os.getenv("GROQ_VISION_MODEL") or "").strip()
    if env_model:
        return env_model
    try:
        from app.models.database_models import SystemSettings
        from app.utils.db import get_db

        db = get_db()
        row = (
            db.query(SystemSettings)
            .filter(SystemSettings.key == "groq_default_model")
            .first()
        )
        admin_model = (row.value or "").strip() if row else ""
        if admin_model.lower().startswith("qwen/"):
            return admin_model
    except Exception:
        pass
    return VISION_MODEL or "qwen/qwen3.8-27b"

# Groq currently allows a small number of images per request.
MAX_IMAGES_PER_REQUEST = int(os.getenv("GROQ_MAX_IMAGES", "3"))

# Render resolutions (DPI). Fail path retries at the higher value.
DPI_DEFAULT = int(os.getenv("PDF_RENDER_DPI", "150"))
DPI_RETRY = int(os.getenv("PDF_RENDER_DPI_RETRY", "300"))

JPEG_QUALITY = int(os.getenv("PDF_RENDER_JPEG_QUALITY", "85"))
MAX_VISION_TOKENS = int(os.getenv("GROQ_VISION_MAX_TOKENS", "16384"))

# Typical MCQ option set is A-D. Incomplete option lists trigger repair.
MIN_OPTIONS = int(os.getenv("MCQ_MIN_OPTIONS", "4"))

# Overlap one page between vision batches so questions that split
# across a page break are seen in full at least once.
BATCH_PAGE_OVERLAP = int(os.getenv("PDF_BATCH_PAGE_OVERLAP", "1"))


# ============================================================
# 1. PYDANTIC STRUCTURED OUTPUT SCHEMA
# ============================================================
#
# Groq strict JSON schema prefers additionalProperties:false
# on every object → extra="forbid" on all nested models.
# ============================================================


class MCQOption(BaseModel):

    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        description=(
            "Option label exactly as used in the PDF, "
            "for example A, B, C, D."
        )
    )

    text: str = Field(
        description=(
            "Complete option content. Mathematical expressions "
            "should use LaTeX when necessary."
        )
    )


class MCQ(BaseModel):

    model_config = ConfigDict(extra="forbid")

    question_no: int = Field(
        description="Question number in the PDF."
    )

    question: str = Field(
        description=(
            "Complete question text. Preserve all mathematical "
            "meaning and use LaTeX for mathematical expressions "
            "when appropriate."
        )
    )

    options: List[MCQOption] = Field(
        description=(
            "All answer options in the same order as the PDF."
        )
    )

    correct_answer: Optional[str] = Field(
        default=None,
        description=(
            "Correct option label from an explicit answer key "
            "in the PDF. Null if no explicit answer key exists."
        )
    )

    domain: Optional[str] = Field(
        default=None,
        description=(
            "Domain explicitly provided in the PDF, otherwise null."
        )
    )

    topic: Optional[str] = Field(
        default=None,
        description=(
            "Topic explicitly provided in the PDF, otherwise null."
        )
    )

    cognitive_level: Optional[str] = Field(
        default=None,
        description=(
            "Cognitive level explicitly provided in the PDF, "
            "otherwise null."
        )
    )

    difficulty: Optional[str] = Field(
        default=None,
        description=(
            "Difficulty explicitly provided in the PDF, "
            "otherwise null."
        )
    )


class QuizExtraction(BaseModel):

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(
        default=None,
        description=(
            "Assessment/document title if explicitly available."
        )
    )

    total_questions: int = Field(
        description=(
            "Total number of actual MCQs returned in questions."
        )
    )

    questions: List[MCQ] = Field(
        description=(
            "All extracted MCQs in their original PDF order."
        )
    )


class AnswerKeyEntry(BaseModel):
    """One row from an explicit answer-key / mapping table."""

    model_config = ConfigDict(extra="forbid")

    question_no: int = Field(
        description="Question number as listed in the answer key."
    )

    correct_answer: Optional[str] = Field(
        default=None,
        description=(
            "Correct option label from the answer key, e.g. A/B/C/D. "
            "Null only if that cell is blank."
        )
    )

    domain: Optional[str] = Field(
        default=None,
        description="Domain from the mapping table if present."
    )

    topic: Optional[str] = Field(
        default=None,
        description="Topic from the mapping table if present."
    )

    cognitive_level: Optional[str] = Field(
        default=None,
        description=(
            "Cognitive level from the mapping table if present."
        )
    )

    difficulty: Optional[str] = Field(
        default=None,
        description="Difficulty from the mapping table if present."
    )


class DocumentEnrichment(BaseModel):
    """Title + answer-key / assessment-mapping enrichment."""

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(
        default=None,
        description="Document title if explicitly present."
    )

    entries: List[AnswerKeyEntry] = Field(
        description=(
            "All answer-key / mapping rows found in the PDF. "
            "Empty list if no explicit answer key exists."
        )
    )


# ============================================================
# EVIDENCE DATACLASSES
# ============================================================


@dataclass
class PageImage:
    page_number: int
    dpi: int
    mime_type: str
    base64_data: str
    width: int
    height: int


@dataclass
class PdfPrecheck:
    path: Path
    page_count: int
    encrypted: bool
    text_chars: int
    has_images: bool
    ok: bool
    messages: List[str] = field(default_factory=list)


# ============================================================
# 2. PDF PRE-CHECK
# ============================================================

def precheck_pdf(pdf_path: str) -> PdfPrecheck:
    """
    Gate the pipeline before spending render/API work.
    Rejects missing, empty, encrypted, or unreadable PDFs.
    """

    path = Path(pdf_path)
    messages: List[str] = []

    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file, got: {path.suffix}")

    if path.stat().st_size == 0:
        raise ValueError(f"PDF file is empty: {path}")

    document = pymupdf.open(str(path))

    try:
        page_count = document.page_count
        encrypted = bool(document.is_encrypted)

        if encrypted:
            raise ValueError(
                f"PDF is encrypted/password-protected: {path}"
            )

        if page_count < 1:
            raise ValueError(f"PDF has no pages: {path}")

        text_chars = 0
        has_images = False

        for page in document:
            text_chars += len(page.get_text("text") or "")
            if page.get_images():
                has_images = True

        if text_chars == 0 and not has_images:
            raise ValueError(
                f"PDF has neither extractable text nor images: {path}"
            )

        if text_chars == 0:
            messages.append(
                "No extractable text — relying on visual evidence "
                "(likely scanned / image-based PDF)."
            )
        elif has_images:
            messages.append(
                "PDF contains both text and embedded images; "
                "hybrid analysis will use both evidence streams."
            )
        else:
            messages.append(
                "Text-layer PDF detected; page renders still used "
                "to recover math layout."
            )

        print(
            f"PDF pre-check passed: {page_count} page(s), "
            f"{text_chars:,} text chars, images={has_images}"
        )

        for message in messages:
            print(f"  - {message}")

        return PdfPrecheck(
            path=path,
            page_count=page_count,
            encrypted=encrypted,
            text_chars=text_chars,
            has_images=has_images,
            ok=True,
            messages=messages,
        )

    finally:
        document.close()


# ============================================================
# 3. TEXT EVIDENCE (PyMuPDF)
# ============================================================

def extract_text_evidence(pdf_path: str) -> Dict:
    """
    Extract:
      1. normal page text
      2. positional text spans

    Positional spans help reconstruct superscripts, subscripts,
    fractions, and stacked math when the text layer alone is
    ambiguous.
    """

    pdf_path = Path(pdf_path)

    print("Extracting TEXT EVIDENCE...")

    document = pymupdf.open(str(pdf_path))
    pages = []

    try:
        for page_number, page in enumerate(document, start=1):
            normal_text = page.get_text("text", sort=True)
            page_dict = page.get_text("dict", sort=True)
            spans = []

            for block in page_dict.get("blocks", []):
                if "lines" not in block:
                    continue

                for line in block.get("lines", []):
                    line_bbox = line.get("bbox")

                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text.strip():
                            continue

                        bbox = span.get("bbox", [0, 0, 0, 0])
                        span_data = {
                            "text": text,
                            "bbox": [
                                round(float(value), 2)
                                for value in bbox
                            ],
                            "font_size": round(
                                float(span.get("size", 0)),
                                2,
                            ),
                            "font": span.get("font", ""),
                            "flags": span.get("flags", 0),
                        }

                        if line_bbox:
                            span_data["line_bbox"] = [
                                round(float(value), 2)
                                for value in line_bbox
                            ]

                        spans.append(span_data)

            pages.append(
                {
                    "page_number": page_number,
                    "page_width": round(float(page.rect.width), 2),
                    "page_height": round(float(page.rect.height), 2),
                    "plain_text": normal_text,
                    "spans": spans,
                }
            )

    finally:
        document.close()

    content = {
        "source_file": pdf_path.name,
        "pages": pages,
    }

    json_chars = len(
        json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    )

    print(
        f"TEXT EVIDENCE ready: {len(pages)} page(s), "
        f"{json_chars:,} JSON characters."
    )

    return content


def pages_as_json_subset(
    text_evidence: Dict,
    page_numbers: List[int],
) -> str:
    """Serialize only the requested pages for a vision batch."""

    wanted = set(page_numbers)
    subset = {
        "source_file": text_evidence.get("source_file"),
        "pages": [
            page
            for page in text_evidence.get("pages", [])
            if page.get("page_number") in wanted
        ],
    }
    return json.dumps(
        subset,
        ensure_ascii=False,
        separators=(",", ":"),
    )


# ============================================================
# 4. VISUAL EVIDENCE (high-res page render)
# ============================================================

def render_pdf_pages(
    pdf_path: str,
    dpi: int = DPI_DEFAULT,
    jpeg_quality: int = JPEG_QUALITY,
) -> List[PageImage]:
    """
    Rasterize each PDF page to a JPEG data-URL payload suitable
    for Groq multimodal chat completions.
    """

    pdf_path = Path(pdf_path)
    print(f"Rendering VISUAL EVIDENCE at {dpi} DPI...")

    document = pymupdf.open(str(pdf_path))
    images: List[PageImage] = []
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)

    try:
        for page_number, page in enumerate(document, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image_bytes = pix.tobytes(
                "jpeg",
                jpg_quality=jpeg_quality,
            )
            encoded = base64.b64encode(image_bytes).decode("ascii")

            images.append(
                PageImage(
                    page_number=page_number,
                    dpi=dpi,
                    mime_type="image/jpeg",
                    base64_data=encoded,
                    width=pix.width,
                    height=pix.height,
                )
            )

            size_kb = len(image_bytes) / 1024.0
            print(
                f"  page {page_number}: "
                f"{pix.width}x{pix.height}px, "
                f"{size_kb:.1f} KB JPEG"
            )

    finally:
        document.close()

    print(f"VISUAL EVIDENCE ready: {len(images)} page image(s).")
    return images


# ============================================================
# 5. GROQ / QWEN VISION MODEL
# ============================================================

def create_vision_llm() -> ChatGroq:
    """
    Qwen multimodal model on Groq for hybrid text+image analysis.
    """
    model = resolve_vision_model()

    return ChatGroq(
        model=model,
        api_key=_require_groq_api_key(),
        temperature=0,
        max_tokens=MAX_VISION_TOKENS,
        # Prefer final answer only; still allow light planning.
        reasoning_effort=os.getenv("GROQ_REASONING_EFFORT", "default"),
        reasoning_format=os.getenv("GROQ_REASONING_FORMAT", "hidden"),
    )


def create_structured_llm(llm: ChatGroq, schema_model: type[BaseModel]):
    """
    Bind Groq native JSON-schema mode (not tool calling).
    """

    return llm.bind(
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema_model.__name__,
                "strict": False,
                "schema": schema_model.model_json_schema(),
            },
        }
    )


def parse_json_model(raw_content: str, schema_model: type[BaseModel]):
    """Parse model JSON content into a validated Pydantic model."""

    content = (raw_content or "").strip()

    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    return schema_model.model_validate(json.loads(content))


def parse_quiz_extraction(raw_content: str) -> QuizExtraction:
    return parse_json_model(raw_content, QuizExtraction)


# ============================================================
# 6. EXTRACTION PROMPT (hybrid text + visual)
# ============================================================

SYSTEM_PROMPT = r"""
You are an expert parser for educational Mathematics
multiple-choice assessment PDFs.

Your task is to reconstruct the assessment faithfully from
DUAL EVIDENCE:

1. TEXT EVIDENCE — PDF plain text + positional spans
2. VISUAL EVIDENCE — high-resolution page images

Return the result according to the provided structured schema.


============================================================
GENERAL RULE
============================================================

The PDF format is NOT fixed.

Different documents may contain:

- different grades
- different subjects
- different numbers of MCQs
- different question numbering
- different numerical values
- different topics
- different metadata
- different answer keys
- different page layouts

NEVER hard-code or assume any specific question, answer,
number, value, topic, or number of questions.


============================================================
INPUT FORMAT (HYBRID)
============================================================

For each PDF page batch you receive:

A. TEXT EVIDENCE (JSON)

  - plain_text
  - spans with bbox / line_bbox / font_size / font / flags

B. VISUAL EVIDENCE (page images)

  - rendered page screenshots for the same page numbers

Use BOTH streams.

- plain_text / spans: reading order, labels, metadata
- page images: true visual math layout (fractions, exponents,
  radicals, stacked expressions, option alignment)

When TEXT and VISUAL disagree on mathematical structure,
prefer the VISUAL layout and use spans only as supporting
coordinates.


============================================================
QUESTION EXTRACTION
============================================================

Identify every actual multiple-choice question.

For each question extract:

- question number
- complete question stem (WITHOUT the leading "Q12." / "12."
  label — that belongs only in question_no)
- ALL answer options that belong to it, even if options
  continue onto the next page inside this batch
- option labels
- domain, if explicitly available on/near the question
- topic, if explicitly available on/near the question
- cognitive level, if explicitly available on/near the question
- difficulty, if explicitly available on/near the question

Many PDFs print a metadata line under each question such as:

  Domain: Numbers | Topic: Laws of Logarithms |
  Cognitive Level: Application | Difficulty: Intermediate

Copy those values into the matching fields when present.

Preserve the original order.

Do not include:

- document headers
- page headers
- footers
- page numbers
- assessment instructions
- cognitive distribution tables
- answer-key rows as questions


============================================================
CROSS-PAGE QUESTIONS (CRITICAL)
============================================================

A single MCQ may start on one page and finish on the next
(for example options A/B on page N and C/D on page N+1).

When both pages are in the current batch:

- emit ONE MCQ with the FULL option set
- never emit a partial question that is missing later options
  if those options are visible in this batch

If a question clearly continues past the last page of this
batch and later options are NOT visible here, still return
the partial question (a later overlapping batch will complete
it). Do NOT invent missing options.


============================================================
MATHEMATICAL ACCURACY IS CRITICAL
============================================================

PDF text extraction often destroys the visual relationship
between mathematical symbols.

DO NOT simply concatenate nearby mathematical text.

Use:

- the page image (primary for layout)
- bbox coordinates
- relative vertical / horizontal position
- font size
- surrounding mathematical context

to reconstruct the expression.


============================================================
SUPERSCRIPTS
============================================================

Small characters positioned above the normal text baseline
may represent exponents.

For example, positional spans visually representing:

x
 2

may mean:

x^2

Return:

$x^2$

Similarly:

z with 4 as exponent

must become:

$z^4$


============================================================
SUBSCRIPTS
============================================================

Characters positioned below the normal baseline may represent
subscripts.

Example:

log with a visually lower a

may represent:

$\log_a$


============================================================
FRACTIONS
============================================================

Vertically stacked mathematical text may represent a fraction.

If the visual layout clearly indicates:

      x + 1
     -------
     x^2 + 4

return:

$\frac{x+1}{x^2+4}$

Do NOT return:

"x + 1 x² + 4"

and do not place numerator and denominator as unrelated text.


Another example:

  3
 ---
  2

must become:

$\frac{3}{2}$

when the coordinates / image clearly show a fraction.


============================================================
COMPLEX FRACTIONS / EXPRESSIONS
============================================================

If the PDF visually represents:

  2 + 3i
 --------
  4 - 2i

return:

$\frac{2+3i}{4-2i}$


If an option visually represents:

1/10 + 4/5 i

return something equivalent to:

$\frac{1}{10}+\frac{4}{5}i$


============================================================
ROOTS
============================================================

Preserve radicals.

Example:

√7

return:

$\sqrt{7}$


============================================================
LOGARITHMS
============================================================

Preserve logarithm bases, powers, arguments, and fractions.

Examples:

log base a of y

return:

$\log_a y$

If an exponent is present, preserve it correctly.


============================================================
LATEX OUTPUT
============================================================

Use LaTeX for mathematical expressions whenever doing so
prevents ambiguity.

Inline mathematical expressions should be enclosed in:

$...$

Examples:

$x^2$

$\sqrt{7}$

$\frac{3}{2}$

$\log_a y$

$\frac{x+1}{x^2+4}$

$\frac{2+3i}{4-2i}$


IMPORTANT:

JSON escaping is handled by the structured-output system.

Your mathematical content must represent the intended LaTeX
expression correctly.


============================================================
DO NOT CHANGE VALUES
============================================================

Never change:

- numbers
- variables
- operators
- signs
- powers
- bases
- numerator
- denominator
- brackets

Do not simplify expressions.

Do not solve questions in order to rewrite them.

Faithful transcription is more important than mathematical
beautification.


============================================================
AMBIGUOUS MATHEMATICS
============================================================

Use page images + positional spans to reconstruct expressions
only when supported by the PDF layout.

Do NOT invent symbols that are absent from the source.

If plain text and spatial / visual layout appear inconsistent,
prioritize the interpretation best supported by the page image
and span positions.

Do not silently replace source content using general
mathematical knowledge.


============================================================
OPTIONS
============================================================

Preserve every option separately.

Example:

A. expression
B. expression
C. expression
D. expression

must become four MCQOption objects.

Never merge options.

Never invent missing options.

If the PDF shows four options, you MUST return four options.
Returning only A/B when C/D are visible (including on the next
page in this batch) is an extraction error.


============================================================
ANSWER KEY
============================================================

Some PDFs contain an explicit answer key / assessment mapping
table after the questions (often titled "Answer Key").

If an explicit answer key exists in this batch:

- map EACH answer-key row to its question number
- set correct_answer from the Answer column ONLY
- also copy Domain / Topic / Cognitive Level / Difficulty
  from the mapping table when those columns exist

Example:

1 C
2 B
3 A

means:

question 1 -> C
question 2 -> B
question 3 -> A


CRITICAL:

Do NOT independently solve a mathematical question to populate
correct_answer.

correct_answer must come ONLY from an explicit answer key
contained in the provided PDF.

If there is no explicit answer key:

correct_answer = null


============================================================
METADATA
============================================================

Extract metadata only when explicitly present.

Sources (in order of usefulness):

1. Per-question metadata lines under the stem
2. Answer-key / assessment-mapping table columns
3. Document title on the first page header

Examples of fields:

Domain
Topic
Cognitive Level
Difficulty
title

If a field is absent:

return null.

Never infer metadata yourself.


============================================================
TOTAL QUESTIONS
============================================================

total_questions must exactly equal:

len(questions)


============================================================
BATCH SCOPE
============================================================

You may receive only a subset of pages in this request.
Extract MCQs that appear (fully or partially) in the provided
pages / images.

- Complete any question whose options span pages inside this
  batch.
- Include answer-key mappings for EVERY question number listed
  in an answer-key table visible in this batch, even if the
  question stem itself is on an earlier page (set
  correct_answer / metadata on those question objects if you
  also return them; otherwise a later enrichment pass will
  apply the key).
- Do not invent questions from pages that are not provided.


============================================================
FINAL QUALITY CHECK
============================================================

Before returning the structured result, internally verify:

1. Every detected MCQ appears exactly once.

2. Question numbering is preserved.

3. Every option belongs to the correct question.

4. Mathematical superscripts are preserved.

5. Mathematical subscripts are preserved.

6. Fractions have correct numerators and denominators.

7. Negative signs have not disappeared.

8. Logarithm bases are preserved.

9. No mathematical values have been changed.

10. correct_answer values come only from an explicit answer
    key.

11. total_questions equals the number of returned questions.

12. No headers, footers, or answer-key table rows have been
    accidentally converted into questions.

13. Question stems do not repeat the "Q12." label.

14. Visible A-D option sets are complete (not truncated).
"""


def _system_message() -> SystemMessage:
    return SystemMessage(content=SYSTEM_PROMPT)


def _build_hybrid_human_message(
    text_json: str,
    page_images: List[PageImage],
    extra_instructions: str = "",
) -> HumanMessage:
    """
    Multimodal human turn: text evidence JSON + page images.
    """

    page_list = ", ".join(
        str(image.page_number) for image in page_images
    )

    extra = ""
    if extra_instructions.strip():
        extra = "\n\n" + extra_instructions.strip() + "\n"

    content = [
        {
            "type": "text",
            "text": (
                "Extract all MCQs from the following hybrid PDF "
                "evidence for page(s): "
                f"{page_list}.\n\n"
                "Use TEXT EVIDENCE for reading order / labels / "
                "metadata and VISUAL EVIDENCE (images below) for "
                "mathematical layout and option completeness. "
                "If a question spans pages inside this batch, "
                "return ONE complete MCQ with ALL options. "
                "Strip leading Q-number labels from the stem."
                f"{extra}\n"
                "<TEXT_EVIDENCE>\n"
                f"{text_json}\n"
                "</TEXT_EVIDENCE>\n\n"
                "Page images follow in page order."
            ),
        }
    ]

    for image in page_images:
        content.append(
            {
                "type": "text",
                "text": (
                    f"[VISUAL EVIDENCE - page {image.page_number} "
                    f"@ {image.dpi} DPI]"
                ),
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:{image.mime_type};base64,"
                        f"{image.base64_data}"
                    ),
                },
            }
        )

    return HumanMessage(content=content)


# ============================================================
# 7. VALIDATE GENERATED JSON SCHEMA
# ============================================================

def validate_schema_configuration():
    """
    Quick local check that Pydantic generated
    additionalProperties:false.
    """

    for name, schema in (
        ("MCQOption", MCQOption.model_json_schema()),
        ("MCQ", MCQ.model_json_schema()),
        ("QuizExtraction", QuizExtraction.model_json_schema()),
        ("AnswerKeyEntry", AnswerKeyEntry.model_json_schema()),
        ("DocumentEnrichment", DocumentEnrichment.model_json_schema()),
    ):
        if schema.get("additionalProperties") is not False:
            raise RuntimeError(
                f"{name} schema does not contain "
                "additionalProperties:false"
            )

    print("Structured-output schema validation passed.")


# ============================================================
# 8. VALIDATOR + CLEANUP
# ============================================================

def _sanitize_text(text: str) -> str:
    """Fix common PDF control-character corruption (e.g. BEL for minus)."""

    if not text:
        return text
    cleaned = text.replace("\u0007", "-").replace("\u0008", "")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned


def _strip_question_prefix(question_no: int, text: str) -> str:
    """Remove leading 'Q19.' / '19.' style labels from the stem."""

    cleaned = _sanitize_text((text or "").strip())
    pattern = re.compile(
        rf"^\s*Q\s*{question_no}\s*[\.\:\-\)]\s*",
        re.IGNORECASE,
    )
    cleaned = pattern.sub("", cleaned, count=1).strip()
    pattern_num = re.compile(
        rf"^\s*{question_no}\s*[\.\:\-\)]\s*"
    )
    cleaned = pattern_num.sub("", cleaned, count=1).strip()
    return cleaned


def cleanup_extraction(result: QuizExtraction) -> QuizExtraction:
    """Deterministic cleanup that does not invent content."""

    cleaned_questions: List[MCQ] = []

    for question in result.questions:
        data = question.model_dump()
        data["question"] = _strip_question_prefix(
            question.question_no,
            question.question,
        )
        for index, option in enumerate(data.get("options") or []):
            option["text"] = _sanitize_text(option.get("text") or "")
            data["options"][index] = option
        for field_name in (
            "domain",
            "topic",
            "cognitive_level",
            "difficulty",
        ):
            if data.get(field_name):
                data[field_name] = _sanitize_text(data[field_name]).strip()
        if data.get("correct_answer"):
            data["correct_answer"] = (
                str(data["correct_answer"]).strip().upper()
            )
        cleaned_questions.append(MCQ.model_validate(data))

    title = result.title
    if title:
        title = _sanitize_text(title).strip()

    return QuizExtraction(
        title=title,
        total_questions=len(cleaned_questions),
        questions=cleaned_questions,
    )


def _pdf_has_answer_key_text(text_evidence: Dict) -> bool:
    for page in text_evidence.get("pages", []):
        plain = (page.get("plain_text") or "").lower()
        if "answer key" in plain:
            return True
        if (
            "answer" in plain
            and "domain" in plain
            and "topic" in plain
            and "cognitive" in plain
        ):
            return True
    return False


def validate_result(
    result: QuizExtraction,
    text_evidence: Optional[Dict] = None,
) -> Tuple[bool, List[str]]:
    """
    Structural validation after hybrid extraction.

    Returns:
        (passed, messages)

    FAIL (passed=False) triggers repair / higher-res retry.
    """

    hard_failures: List[str] = []
    warnings: List[str] = []

    actual_count = len(result.questions)

    if actual_count == 0:
        hard_failures.append("No MCQs were extracted.")

    if result.total_questions != actual_count:
        warnings.append(
            f"total_questions={result.total_questions}, "
            f"but {actual_count} questions were returned."
        )

    question_numbers = [
        question.question_no for question in result.questions
    ]

    duplicate_numbers = sorted(
        {
            number
            for number in question_numbers
            if question_numbers.count(number) > 1
        }
    )

    if duplicate_numbers:
        hard_failures.append(
            "Duplicate question numbers: "
            + ", ".join(map(str, duplicate_numbers))
        )

    answered = 0

    for question in result.questions:
        if not question.question or not question.question.strip():
            hard_failures.append(
                f"Q{question.question_no}: empty question text."
            )

        if re.match(
            rf"^\s*Q\s*{question.question_no}\s*[\.\:\-\)]",
            question.question or "",
            re.IGNORECASE,
        ):
            warnings.append(
                f"Q{question.question_no}: stem still contains "
                "a leading Q-number prefix."
            )

        if not question.options:
            hard_failures.append(
                f"Q{question.question_no}: no options extracted."
            )
            continue

        if len(question.options) < MIN_OPTIONS:
            hard_failures.append(
                f"Q{question.question_no}: only "
                f"{len(question.options)} option(s); "
                f"expected at least {MIN_OPTIONS} "
                "(likely a cross-page split)."
            )

        labels = [
            option.label.upper().strip()
            for option in question.options
        ]

        if len(labels) != len(set(labels)):
            hard_failures.append(
                f"Q{question.question_no}: duplicate option labels."
            )

        empty_options = [
            option.label
            for option in question.options
            if not (option.text or "").strip()
        ]
        if empty_options:
            hard_failures.append(
                f"Q{question.question_no}: empty option text for "
                + ", ".join(empty_options)
            )

        if question.correct_answer:
            answered += 1
            correct = question.correct_answer.upper().strip()
            if correct not in labels:
                hard_failures.append(
                    f"Q{question.question_no}: "
                    f"correct_answer={correct} "
                    "does not match an extracted option."
                )

    if text_evidence is not None and _pdf_has_answer_key_text(
        text_evidence
    ):
        if answered == 0:
            hard_failures.append(
                "PDF contains an Answer Key but no "
                "correct_answer values were mapped."
            )
        elif answered < actual_count:
            warnings.append(
                f"Answer key mapped for {answered}/{actual_count} "
                "questions."
            )

    messages = [
        f"FAIL: {item}" for item in hard_failures
    ] + [
        f"WARN: {item}" for item in warnings
    ]

    passed = len(hard_failures) == 0
    return passed, messages


# ============================================================
# 9. HYBRID ANALYSIS (Qwen Vision / Groq)
# ============================================================

def _chunk_pages(
    page_images: List[PageImage],
    chunk_size: int = MAX_IMAGES_PER_REQUEST,
    overlap: int = BATCH_PAGE_OVERLAP,
) -> List[List[PageImage]]:
    """
    Sliding window over pages with overlap so MCQs that split
    across a page break appear complete in at least one batch.
    """

    if not page_images:
        return []

    chunk_size = max(1, chunk_size)
    overlap = max(0, min(overlap, chunk_size - 1))
    stride = max(1, chunk_size - overlap)

    batches: List[List[PageImage]] = []
    index = 0
    total = len(page_images)

    while index < total:
        batch = page_images[index:index + chunk_size]
        if not batch:
            break
        batches.append(batch)
        if index + chunk_size >= total:
            break
        index += stride

    return batches


def _question_richness(question: MCQ) -> tuple:
    """Higher score wins when merging duplicate question_no."""

    return (
        len(question.options),
        sum(len((opt.text or "").strip()) for opt in question.options),
        len((question.question or "").strip()),
        int(bool(question.correct_answer)),
        int(bool(question.domain)),
        int(bool(question.topic)),
        int(bool(question.cognitive_level)),
        int(bool(question.difficulty)),
    )


def _merge_question_pair(current: MCQ, incoming: MCQ) -> MCQ:
    """
    Prefer the richer extraction; fill null metadata / answers
    from the other copy when useful.
    """

    if _question_richness(incoming) > _question_richness(current):
        primary, secondary = incoming, current
    else:
        primary, secondary = current, incoming

    data = primary.model_dump()

    # Prefer the fuller option list; if equal length, keep primary.
    if len(secondary.options) > len(primary.options):
        data["options"] = [
            option.model_dump() for option in secondary.options
        ]
    elif (
        len(secondary.options) == len(primary.options)
        and len(secondary.options) > 0
    ):
        # Merge option text if primary text is shorter/empty.
        merged_options = []
        secondary_by_label = {
            opt.label.upper().strip(): opt
            for opt in secondary.options
        }
        for option in primary.options:
            other = secondary_by_label.get(option.label.upper().strip())
            text = option.text
            if other and len((other.text or "").strip()) > len(
                (text or "").strip()
            ):
                text = other.text
            merged_options.append(
                {
                    "label": option.label,
                    "text": text,
                }
            )
        data["options"] = merged_options

    for field_name in (
        "correct_answer",
        "domain",
        "topic",
        "cognitive_level",
        "difficulty",
    ):
        if not data.get(field_name) and getattr(secondary, field_name):
            data[field_name] = getattr(secondary, field_name)

    if len((secondary.question or "").strip()) > len(
        (data.get("question") or "").strip()
    ):
        data["question"] = secondary.question

    return MCQ.model_validate(data)


def _merge_extractions(
    parts: List[QuizExtraction],
) -> QuizExtraction:
    """Merge per-batch results; prefer complete over partial MCQs."""

    title = None
    by_number: Dict[int, MCQ] = {}

    for part in parts:
        if title is None and part.title:
            title = part.title

        for question in part.questions:
            existing = by_number.get(question.question_no)
            if existing is None:
                by_number[question.question_no] = question
            else:
                by_number[question.question_no] = _merge_question_pair(
                    existing,
                    question,
                )

    merged_questions = [
        by_number[number]
        for number in sorted(by_number)
    ]

    return QuizExtraction(
        title=title,
        total_questions=len(merged_questions),
        questions=merged_questions,
    )


def _pages_by_number(
    page_images: List[PageImage],
) -> Dict[int, PageImage]:
    return {image.page_number: image for image in page_images}


def _find_pages_mentioning_question(
    text_evidence: Dict,
    question_no: int,
) -> List[int]:
    """Heuristic: pages whose plain text mention Q{n} / '{n}.' """

    patterns = [
        re.compile(rf"\bQ\s*{question_no}\b", re.IGNORECASE),
        re.compile(rf"(?m)^\s*{question_no}\s*[\.\:\)]"),
    ]
    hits: List[int] = []

    for page in text_evidence.get("pages", []):
        plain = page.get("plain_text") or ""
        if any(pattern.search(plain) for pattern in patterns):
            hits.append(int(page["page_number"]))

    return hits


def _answer_key_page_numbers(text_evidence: Dict) -> List[int]:
    pages: List[int] = []
    for page in text_evidence.get("pages", []):
        plain = (page.get("plain_text") or "").lower()
        if "answer key" in plain:
            pages.append(int(page["page_number"]))
        elif (
            "answer" in plain
            and "domain" in plain
            and "topic" in plain
            and "cognitive" in plain
            and int(page["page_number"])
            >= max(1, len(text_evidence.get("pages", [])) - 1)
        ):
            pages.append(int(page["page_number"]))
    return sorted(set(pages))



_COGNITIVE_LEVELS = {
    "knowledge",
    "understanding",
    "application",
    "analysis",
    "reasoning",
    "analysis/reasoning",
    "analysis / reasoning",
}


def _looks_like_cognitive_level(line: str) -> bool:
    normalized = re.sub(r"\s+", " ", (line or "").strip().lower())
    if normalized in _COGNITIVE_LEVELS:
        return True
    compact = normalized.replace(" ", "")
    return compact in {
        item.replace(" ", "") for item in _COGNITIVE_LEVELS
    }


def _plain_text_for_pages(
    text_evidence: Dict,
    page_numbers: List[int],
) -> str:
    wanted = set(page_numbers)
    chunks = []
    for page in text_evidence.get("pages", []):
        if page.get("page_number") in wanted:
            chunks.append(page.get("plain_text") or "")
    return "\n\n".join(chunks)


_COGNITIVE_PATTERN = re.compile(
    r"(Knowledge|Understanding|Application|Analysis\s*/\s*Reasoning|"
    r"Analysis|Reasoning)\s*$",
    re.IGNORECASE,
)

_ROW_PATTERN = re.compile(
    r"^(\d{1,3})\s+([A-Da-d])\s+(\S+)\s+(.*)$"
)


def parse_answer_key_from_text(text_evidence: Dict) -> DocumentEnrichment:
    """
    Parse Answer Key / Assessment Mapping pages.

    Supports compact rows:
      Q  Answer  Domain  Topic  Cognitive Level
    with topic wrap lines, and a one-field-per-line fallback.
    """

    key_pages = _answer_key_page_numbers(text_evidence)
    if not key_pages:
        return DocumentEnrichment(title=None, entries=[])

    raw = _plain_text_for_pages(text_evidence, key_pages)
    lines = [
        _sanitize_text(line).strip()
        for line in raw.splitlines()
        if _sanitize_text(line).strip()
    ]

    tabular_entries: List[AnswerKeyEntry] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _ROW_PATTERN.match(line)
        if not match:
            index += 1
            continue

        question_no = int(match.group(1))
        correct = match.group(2).upper()
        domain = match.group(3).strip()
        rest = match.group(4).strip()

        cognitive = None
        topic = rest
        cog = _COGNITIVE_PATTERN.search(rest)
        if cog:
            cognitive = re.sub(
                r"(?i)analysis\s*/\s*reasoning",
                "Analysis/Reasoning",
                cog.group(1),
            ).strip()
            topic = rest[:cog.start()].strip()

        index += 1
        while index < len(lines):
            nxt = lines[index]
            if _ROW_PATTERN.match(nxt) or re.fullmatch(r"\d{1,3}", nxt):
                break
            low = nxt.lower()
            if "answer key" in low:
                break
            if low.startswith("q") and "answer" in low and "domain" in low:
                break
            topic = f"{topic} {nxt}".strip()
            index += 1

        tabular_entries.append(
            AnswerKeyEntry(
                question_no=question_no,
                correct_answer=correct,
                domain=domain or None,
                topic=topic or None,
                cognitive_level=cognitive,
                difficulty=None,
            )
        )

    if sum(1 for entry in tabular_entries if entry.correct_answer) >= 1:
        entries = tabular_entries
    else:
        entries = []
        start = 0
        for i, line in enumerate(lines):
            if re.fullmatch(r"\d{1,3}", line):
                start = i
                break
        index = start
        while index < len(lines):
            if not re.fullmatch(r"\d{1,3}", lines[index]):
                index += 1
                continue
            question_no = int(lines[index])
            index += 1
            correct = None
            if index < len(lines) and re.fullmatch(r"[A-Da-d]", lines[index]):
                correct = lines[index].upper()
                index += 1
            domain = None
            if index < len(lines) and not re.fullmatch(
                r"\d{1,3}", lines[index]
            ):
                domain = lines[index]
                index += 1
            topic_parts: List[str] = []
            while index < len(lines):
                line = lines[index]
                if re.fullmatch(r"\d{1,3}", line):
                    break
                if _looks_like_cognitive_level(line):
                    break
                topic_parts.append(line)
                index += 1
            topic = " ".join(topic_parts).strip() or None
            cognitive = None
            if index < len(lines) and _looks_like_cognitive_level(
                lines[index]
            ):
                cognitive = lines[index]
                index += 1
            entries.append(
                AnswerKeyEntry(
                    question_no=question_no,
                    correct_answer=correct,
                    domain=domain,
                    topic=topic,
                    cognitive_level=cognitive,
                    difficulty=None,
                )
            )

    title = None
    first_page = _plain_text_for_pages(text_evidence, [1])
    title_parts: List[str] = []
    for line in first_page.splitlines():
        candidate = _sanitize_text(line).strip()
        if not candidate or candidate.startswith("---"):
            continue
        low = candidate.lower()
        if "answer key" in low:
            continue
        if low.startswith(
            ("coverage", "domains", "time", "total", "q1", "q 1")
        ):
            break
        title_parts.append(candidate)
        if len(title_parts) >= 2:
            break
    if title_parts:
        title = " - ".join(title_parts)

    return DocumentEnrichment(title=title, entries=entries)



def _apply_enrichment(
    result: QuizExtraction,
    enrichment: DocumentEnrichment,
) -> QuizExtraction:
    by_entry = {
        entry.question_no: entry for entry in enrichment.entries
    }

    updated: List[MCQ] = []
    for question in result.questions:
        data = question.model_dump()
        entry = by_entry.get(question.question_no)
        if entry:
            if entry.correct_answer:
                data["correct_answer"] = (
                    entry.correct_answer.strip().upper()
                )
            for field_name in (
                "domain",
                "topic",
                "cognitive_level",
                "difficulty",
            ):
                value = getattr(entry, field_name)
                if value and not data.get(field_name):
                    data[field_name] = value
                elif value and field_name in (
                    "domain",
                    "cognitive_level",
                    "difficulty",
                ):
                    # Prefer explicit answer-key mapping for these.
                    data[field_name] = value
        updated.append(MCQ.model_validate(data))

    title = result.title
    if enrichment.title:
        bad_existing = (
            not title
            or title.strip().startswith("---")
            or title.strip().lower() in {"none", "null"}
        )
        if bad_existing or len(enrichment.title) > len(title or ""):
            title = enrichment.title

    return QuizExtraction(
        title=title,
        total_questions=len(updated),
        questions=updated,
    )


def enrich_with_answer_key(
    result: QuizExtraction,
    text_evidence: Dict,
    page_images: List[PageImage],
) -> QuizExtraction:
    """
    Map correct_answer + metadata from the Answer Key page.

    Prefer a deterministic text parse of columnar keys (reliable),
    then fall back to a hybrid vision pass if answers are still
    missing.
    """

    key_pages = _answer_key_page_numbers(text_evidence)
    if not key_pages:
        all_pages = [
            int(page["page_number"])
            for page in text_evidence.get("pages", [])
        ]
        if not all_pages:
            return result
        key_pages = [max(all_pages)]

    print(
        f"Enrichment pass: answer-key / mapping page(s) {key_pages}"
    )

    deterministic = parse_answer_key_from_text(text_evidence)
    answered = sum(
        1 for entry in deterministic.entries if entry.correct_answer
    )
    print(
        f"  Deterministic key rows: {len(deterministic.entries)} "
        f"({answered} with answers); title={deterministic.title!r}"
    )

    enriched = _apply_enrichment(result, deterministic)

    still_missing = sum(
        1 for question in enriched.questions if not question.correct_answer
    )
    if answered > 0 and still_missing == 0:
        return enriched

    # Vision fallback when the columnar parse is incomplete.
    print(
        f"  Vision enrichment fallback "
        f"(still missing answers for {still_missing} question(s))..."
    )

    by_number = _pages_by_number(page_images)
    images = [
        by_number[number]
        for number in key_pages
        if number in by_number
    ][:MAX_IMAGES_PER_REQUEST]

    plain = _plain_text_for_pages(text_evidence, key_pages)
    first_plain = _plain_text_for_pages(text_evidence, [1])

    llm = create_vision_llm()
    structured_llm = create_structured_llm(llm, DocumentEnrichment)

    content = [
        {
            "type": "text",
            "text": (
                "Extract the document title (if any) and EVERY row "
                "from the explicit Answer Key / Assessment Mapping "
                "table.\n\n"
                "The text may list one field per line in this order:\n"
                "question_no, Answer (A-D), Domain, Topic "
                "(possibly multiple lines), Cognitive Level.\n\n"
                "Do NOT solve questions. Only copy values that appear "
                "in the key/mapping. correct_answer must be the "
                "Answer column letter.\n\n"
                "<FIRST_PAGE_TEXT>\n"
                f"{first_plain}\n"
                "</FIRST_PAGE_TEXT>\n\n"
                "<ANSWER_KEY_TEXT>\n"
                f"{plain}\n"
                "</ANSWER_KEY_TEXT>\n"
            ),
        }
    ]

    for image in images:
        content.append(
            {
                "type": "text",
                "text": (
                    f"[VISUAL EVIDENCE - answer key page "
                    f"{image.page_number}]"
                ),
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:{image.mime_type};base64,"
                        f"{image.base64_data}"
                    ),
                },
            }
        )

    raw = structured_llm.invoke(
        [
            SystemMessage(
                content=(
                    "You extract answer keys and assessment mapping "
                    "tables from exam PDFs using text + page images. "
                    "Never invent answers. If no answer key exists, "
                    "return an empty entries list. Always set "
                    "correct_answer to the Answer column letter "
                    "when present."
                )
            ),
            HumanMessage(content=content),
        ]
    )

    enrichment = parse_json_model(raw.content, DocumentEnrichment)
    print(
        f"  Vision enrichment rows: {len(enrichment.entries)}; "
        f"title={enrichment.title!r}"
    )

    return _apply_enrichment(enriched, enrichment)


_INLINE_META_PATTERN = re.compile(
    r"Domain\s*:\s*(.*?)\s*\|\s*Topic\s*:\s*(.*?)\s*\|\s*"
    r"Cognitive Level\s*:\s*(.*?)\s*\|\s*"
    r"(?:Difficulty|Intended difficulty)\s*:\s*([^\n|]+)",
    re.IGNORECASE,
)


def enrich_inline_question_metadata(
    result: QuizExtraction,
    text_evidence: Dict,
) -> QuizExtraction:
    """
    Fill domain/topic/cognitive/difficulty from per-question
    metadata lines that sit under each stem in the PDF text.
    """

    full_text = "\n".join(
        page.get("plain_text") or ""
        for page in text_evidence.get("pages", [])
    )

    updated: List[MCQ] = []
    for question in result.questions:
        data = question.model_dump()
        # Slice text from this question marker to the next.
        start_match = re.search(
            rf"(?im)^\s*Q\s*{question.question_no}\s*[\.\:\)]",
            full_text,
        )
        if start_match:
            start = start_match.end()
            next_match = re.search(
                rf"(?im)^\s*Q\s*{question.question_no + 1}\s*[\.\:\)]",
                full_text[start:],
            )
            chunk = (
                full_text[start:start + next_match.start()]
                if next_match
                else full_text[start:start + 800]
            )
            meta = _INLINE_META_PATTERN.search(chunk)
            if meta:
                domain, topic, cognitive, difficulty = [
                    _sanitize_text(group).strip() or None
                    for group in meta.groups()
                ]
                if domain and not data.get("domain"):
                    data["domain"] = domain
                if topic and not data.get("topic"):
                    data["topic"] = topic
                if cognitive and not data.get("cognitive_level"):
                    data["cognitive_level"] = cognitive
                if difficulty:
                    # Prefer explicit per-question difficulty.
                    data["difficulty"] = difficulty.rstrip(" .")

        updated.append(MCQ.model_validate(data))

    return QuizExtraction(
        title=result.title,
        total_questions=len(updated),
        questions=updated,
    )


def repair_incomplete_questions(
    result: QuizExtraction,
    text_evidence: Dict,
    page_images: List[PageImage],
) -> QuizExtraction:
    """
    Re-run hybrid extraction only for MCQs that still have too
    few options, using neighboring pages around the split.
    """

    incomplete = [
        question
        for question in result.questions
        if len(question.options) < MIN_OPTIONS
    ]
    if not incomplete:
        return result

    print(
        "Repair pass for incomplete option sets: "
        + ", ".join(f"Q{q.question_no}" for q in incomplete)
    )

    by_number = _pages_by_number(page_images)
    all_page_numbers = sorted(by_number)
    llm = create_vision_llm()
    structured_llm = create_structured_llm(llm, QuizExtraction)
    system = _system_message()
    repaired_parts: List[QuizExtraction] = []

    for question in incomplete:
        mentioned = _find_pages_mentioning_question(
            text_evidence,
            question.question_no,
        )
        if not mentioned:
            # Fallback: use middle pages — still better than nothing.
            mentioned = all_page_numbers[
                max(0, len(all_page_numbers) // 2 - 1):
                max(0, len(all_page_numbers) // 2 - 1) + 2
            ]

        # Include neighboring pages so A/B + C/D page splits are seen.
        window = set()
        for page_no in mentioned:
            for delta in (-1, 0, 1):
                candidate = page_no + delta
                if candidate in by_number:
                    window.add(candidate)

        selected_pages = sorted(window)[:MAX_IMAGES_PER_REQUEST]
        batch = [by_number[number] for number in selected_pages]
        text_json = pages_as_json_subset(text_evidence, selected_pages)

        human = _build_hybrid_human_message(
            text_json,
            batch,
            extra_instructions=(
                f"FOCUS: Reconstruct question {question.question_no} "
                f"completely with ALL options (at least {MIN_OPTIONS}). "
                "It may span two pages. Return that question with a "
                "full option list; other questions optional."
            ),
        )

        print(
            f"  Repair Q{question.question_no} using pages "
            f"{selected_pages}..."
        )
        raw = structured_llm.invoke([system, human])
        part = parse_quiz_extraction(raw.content)
        repaired_parts.append(part)

    if not repaired_parts:
        return result

    return _merge_extractions([result, *repaired_parts])


def hybrid_analyze(
    text_evidence: Dict,
    page_images: List[PageImage],
) -> QuizExtraction:
    """
    TEXT + VISUAL evidence -> Qwen Vision on Groq (overlapping
    batches) -> merge -> cleanup -> answer-key enrichment ->
    incomplete-option repair.
    """

    validate_schema_configuration()

    llm = create_vision_llm()
    structured_llm = create_structured_llm(llm, QuizExtraction)
    system = _system_message()

    batches = _chunk_pages(page_images)
    parts: List[QuizExtraction] = []

    print(
        f"Hybrid analysis via {resolve_vision_model()}: "
        f"{len(page_images)} page image(s) in "
        f"{len(batches)} overlapping batch(es) "
        f"(max {MAX_IMAGES_PER_REQUEST} images/request, "
        f"overlap={BATCH_PAGE_OVERLAP})."
    )

    for batch_index, batch in enumerate(batches, start=1):
        page_numbers = [image.page_number for image in batch]
        text_json = pages_as_json_subset(text_evidence, page_numbers)
        human = _build_hybrid_human_message(text_json, batch)

        print(
            f"  Batch {batch_index}/{len(batches)}: "
            f"pages {page_numbers} -> Groq..."
        )

        raw_response = structured_llm.invoke([system, human])
        part = parse_quiz_extraction(raw_response.content)
        print(
            f"  Batch {batch_index}: "
            f"{len(part.questions)} question(s)."
        )
        parts.append(part)

    merged = cleanup_extraction(_merge_extractions(parts))
    merged = enrich_with_answer_key(
        merged,
        text_evidence,
        page_images,
    )
    merged = enrich_inline_question_metadata(merged, text_evidence)
    merged = cleanup_extraction(merged)
    merged = repair_incomplete_questions(
        merged,
        text_evidence,
        page_images,
    )
    merged = cleanup_extraction(merged)

    print(
        f"Hybrid analysis complete: "
        f"{merged.total_questions} total question(s)."
    )
    return merged


# ============================================================
# 10. MAIN PIPELINE
# ============================================================

def extract_mcqs(
    pdf_path: str,
    dpi: int = DPI_DEFAULT,
    retry_dpi: int = DPI_RETRY,
    allow_retry: bool = True,
) -> QuizExtraction:
    """
    Full architecture:

      pre-check -> dual evidence -> hybrid analysis
      -> enrich/repair -> validate
      -> final JSON  |  higher-res retry on FAIL
    """

    print("=" * 60)
    print("MCQ PIPELINE - dual evidence hybrid extraction")
    print("=" * 60)

    precheck_pdf(pdf_path)

    text_evidence = extract_text_evidence(pdf_path)
    page_images = render_pdf_pages(pdf_path, dpi=dpi)

    result = hybrid_analyze(text_evidence, page_images)

    passed, messages = validate_result(result, text_evidence)

    if messages:
        print("\nValidator:")
        for message in messages:
            print(f"  - {message}")
    else:
        print("\nValidator: PASS (no issues).")

    if passed:
        print("Validator: PASS -> Final JSON")
        return result

    if not allow_retry or retry_dpi <= dpi:
        print(
            "Validator: FAIL - no higher-res retry available; "
            "returning best-effort result."
        )
        return result

    print(
        f"\nValidator: FAIL -> higher-res retry at {retry_dpi} DPI..."
    )

    retry_images = render_pdf_pages(pdf_path, dpi=retry_dpi)
    retry_result = hybrid_analyze(text_evidence, retry_images)
    retry_passed, retry_messages = validate_result(
        retry_result,
        text_evidence,
    )

    if retry_messages:
        print("\nValidator (retry):")
        for message in retry_messages:
            print(f"  - {message}")

    if retry_passed:
        print("Validator: PASS after higher-res retry -> Final JSON")
        return retry_result

    print(
        "Validator: still FAIL after higher-res retry; "
        "returning retry result."
    )
    return retry_result


# ============================================================
# 11. SAVE JSON
# ============================================================

def save_json(
    result: QuizExtraction,
    output_path: str,
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = result.model_dump()
    # Structural bookkeeping only — do not alter question content.
    data["total_questions"] = len(data["questions"])

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(f"JSON saved to: {output_path}")


# ============================================================
# 12. OPTIONAL PRETTY PRINT
# ============================================================

def print_summary(result: QuizExtraction):
    print()
    print("=" * 60)
    print("EXTRACTION SUMMARY")
    print("=" * 60)
    print(f"Title: {result.title}")
    print(f"Questions: {len(result.questions)}")
    print()

    for question in result.questions:
        print(f"Q{question.question_no}: {question.question}")
        for option in question.options:
            print(f"    {option.label}. {option.text}")
        print("    Correct:", question.correct_answer)
        print("-" * 60)


# ============================================================
# 13. RUN
# ============================================================

if __name__ == "__main__":

    pdf_path = (
        r"C:\Users\user\Desktop\iqbalai-v1.1"
        r"\DIL_SAATHI_Baseline_Diagnostic_Mathematics IX (1).pdf"
    )

    output_path = "mcqs_output.json"

    try:
        result = extract_mcqs(pdf_path)
        save_json(result, output_path)
        print_summary(result)

    except FileNotFoundError as error:
        print(f"\nFile error: {error}")

    except Exception as error:
        print(f"\nExtraction failed: {error}")
        raise
