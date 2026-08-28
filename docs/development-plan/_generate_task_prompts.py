#!/usr/bin/env python3
"""Generate development-plan task markdown files with Cursor implementation prompts."""
from __future__ import annotations

import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]  # iqbal_ai_stg

COMMON_CONTEXT = """
## Project Context

- **Application:** IqbalAI (`iqbal_ai_stg`) — Flask monolith, PostgreSQL, Redis/Celery, RAG (Milvus/Chroma), LangChain/LangGraph
- **Entry point:** `run.py` → `app/__init__.py`
- **ORM models:** `app/models/database_models.py`
- **Domain accessors:** `app/models/models.py`
- **Config:** `app/config.py`, `.env`
- **RBAC:** `app/rbac/permissions.py`, `app/rbac/decorators.py`
- **Migrations:** No Alembic — use SQL scripts in `docs/development-plan/migrations/` and update `app/utils/db.py` `init_db()`
- **Existing patterns:** Follow `app/services/lesson/models.py` for Pydantic; follow `app/routes/lesson_routes.py` for API blueprints
- **Do NOT** duplicate existing auth, lesson CMS, or RAG ingest — extend and reuse
""".strip()


def md_task(
    task_id: str,
    title: str,
    phase: str,
    phase_folder: str,
    task_type: str,
    priority: str,
    dependencies: list[str],
    description: str,
    objective: str,
    requirements: list[str],
    files_create: list[str],
    files_modify: list[str],
    acceptance: list[str],
    constraints: list[str],
    cursor_prompt: str,
    mvp: bool = False,
) -> str:
    deps = ", ".join(dependencies) if dependencies else "None"
    req_block = "\n".join(f"- {r}" for r in requirements)
    acc_block = "\n".join(f"- [ ] {a}" for a in acceptance)
    con_block = "\n".join(f"- {c}" for c in constraints)
    create_block = "\n".join(f"- `{f}`" for f in files_create) or "- (determine during implementation)"
    modify_block = "\n".join(f"- `{f}`" for f in files_modify) or "- (none expected)"

    return f"""# {task_id} — {title}

| Field | Value |
|-------|-------|
| **Phase** | {phase} |
| **Type** | {task_type} |
| **Priority** | {priority} |
| **MVP** | {"Yes" if mvp else "No"} |
| **Dependencies** | {deps} |

## Description

{description}

## Objective

{objective}

{COMMON_CONTEXT}

## Requirements

{req_block}

## Files to Create

{create_block}

## Files to Modify

{modify_block}

## Acceptance Criteria

{acc_block}

## Constraints

{con_block}

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
{cursor_prompt.strip()}
```
"""


# Task definitions: (task_id, slug, phase_name, folder, ...kwargs as dict)
TASKS: list[dict] = []

def task(**kwargs):
    if "title" not in kwargs and "slug" in kwargs:
        kwargs["title"] = kwargs["slug"].replace("-", " ").title()
    cp = kwargs.get("cursor_prompt")
    if isinstance(cp, list):
        kwargs["cursor_prompt"] = "\n".join(str(x) for x in cp)
    TASKS.append(kwargs)


# ─── PHASE 0 ───────────────────────────────────────────────────────────────
task(
    task_id="P0-001",
    slug="existing-system-analysis",
    title="Existing System Analysis",
    phase="Phase 0 — Existing System Analysis",
    phase_folder="phase-0-existing-system",
    task_type="Documentation",
    priority="P0",
    dependencies=[],
    mvp=True,
    description="Reference document summarizing what exists in the codebase vs what must be built for the LMS roadmap.",
    objective="Produce a living analysis doc developers can reference before implementing any phase.",
    requirements=[
        "Document existing auth, roles, lessons, RAG, chat, RBAC, and database models",
        "List reusable components and explicit gaps (classes, assignments, question bank, mastery)",
        "Note: no Alembic; monolithic Jinja dashboards",
    ],
    files_create=["docs/development-plan/phase-0-existing-system/ARCHITECTURE_BASELINE.md"],
    files_modify=[],
    acceptance=[
        "Architecture baseline doc exists and matches current codebase",
        "Gap list aligns with development-plan phases 1–10",
    ],
    constraints=[
        "Read-only analysis — do not implement features in this task",
        "Do not include secrets from .env",
    ],
    cursor_prompt="""
You are a senior software architect documenting the IqbalAI Flask codebase at iqbal_ai_stg.

TASK: Create ARCHITECTURE_BASELINE.md in docs/development-plan/phase-0-existing-system/

Analyze the actual codebase (do not assume). Include:
1. Tech stack and folder structure
2. Existing user roles and auth flow
3. All SQLAlchemy models in app/models/database_models.py
4. Existing API blueprints and key endpoints
5. AI/RAG/LangGraph capabilities
6. What is MISSING for the LMS roadmap (classes, question bank, assignments, mastery, PDF→MCQ pipeline)
7. Reusable patterns (Pydantic in app/services/lesson/models.py, RAG ingest, RBAC)

Keep it factual with file paths. No code changes except the markdown file.
""",
)

# ─── PHASE 1 ───────────────────────────────────────────────────────────────
task(
    task_id="F-001",
    slug="curriculum-taxonomy-schema",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=[],
    mvp=True,
    description="Define subjects → topics → subtopics with optional prerequisites and difficulty levels.",
    objective="Create the curriculum taxonomy that all questions, quizzes, diagnostics, and mastery tracking depend on.",
    requirements=[
        "Create `topics` table: id, name, slug, parent_id (self-FK), subject, grade_level, description, sort_order, is_active",
        "Create `topic_prerequisites` table: topic_id, prerequisite_topic_id",
        "Add indexes on parent_id, subject, slug",
        "Create seed script for Math topics: Algebra, Fractions, Geometry, Word Problems, Quadratic Equations",
        "Register models in database_models.py",
    ],
    files_create=[
        "docs/development-plan/migrations/001_topics.sql",
        "app/services/lms/curriculum_service.py",
        "scripts/seed_topics.py",
    ],
    files_modify=["app/models/database_models.py", "app/utils/db.py"],
    acceptance=[
        "Topics and prerequisites tables exist in PostgreSQL/SQLite dev",
        "Seed script populates Math topics",
        "curriculum_service can list topics by subject and get prerequisites",
    ],
    constraints=[
        "Do not break existing models or init_db",
        "Use same SQLAlchemy Base from database_models.py",
        "Slug must be unique per subject",
    ],
    cursor_prompt="""
You are a senior backend engineer implementing F-001 for IqbalAI LMS foundation.

TASK: Implement curriculum taxonomy schema (topics + prerequisites).

CONTEXT:
- ORM: app/models/database_models.py (SQLAlchemy declarative Base)
- DB init: app/utils/db.py init_db() — no Alembic
- Follow existing model patterns (User, Lesson tables)

IMPLEMENT:
1. Add Topic and TopicPrerequisite SQLAlchemy models to database_models.py
2. Create docs/development-plan/migrations/001_topics.sql with CREATE TABLE IF NOT EXISTS
3. Update init_db() to run migration SQL safely (check table exists)
4. Create app/services/lms/__init__.py and curriculum_service.py with:
   - list_topics(subject, grade_level=None)
   - get_topic_by_id(id)
   - get_prerequisites(topic_id)
5. Create scripts/seed_topics.py to seed Math topics including Quadratic Equations

SEED TOPICS (minimum): Algebra, Fractions, Geometry, Word Problems, Quadratic Equations

Do not implement API routes yet. Add minimal unit tests if tests/ pattern exists.

Verify: run seed script against dev DB without errors.
""",
)

task(
    task_id="F-002",
    slug="question-bank-schema",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=["F-001"],
    mvp=True,
    description="Normalized question bank for MCQ storage independent of lesson JSON.",
    objective="Single source of truth for all quiz/diagnostic questions.",
    requirements=[
        "Create `questions` table linked to topic_id and created_by (user FK)",
        "Fields: question_text, question_latex (nullable), explanation, difficulty, is_active",
        "Support MCQ: options JSON array (4 strings), correct_option_index (0-3)",
        "Store correct_answer_raw for PDF-sourced answers",
    ],
    files_create=[
        "docs/development-plan/migrations/002_questions.sql",
        "app/services/lms/question_bank_service.py",
    ],
    files_modify=["app/models/database_models.py", "app/utils/db.py"],
    acceptance=[
        "questions table exists with FK to topics and users",
        "question_bank_service CRUD: create, get, list_by_topic, soft-delete",
    ],
    constraints=[
        "correct_option_index must be 0-3 when options length is 4",
        "Do not migrate lesson assessment_quiz yet (separate task A-312)",
    ],
    cursor_prompt="""
You are a senior backend engineer implementing F-002 Question Bank schema for IqbalAI.

DEPENDS ON: F-001 (topics table must exist)

TASK: Create normalized questions table and question_bank_service.

IMPLEMENT:
1. Question SQLAlchemy model in database_models.py:
   - topic_id FK, created_by FK, question_text, question_latex, options (JSON/Text), correct_option_index
   - correct_answer_raw, explanation, difficulty (easy/medium/hard), is_active, timestamps
2. Migration 002_questions.sql
3. app/services/lms/question_bank_service.py with create/read/update/list/filter by topic
4. Pydantic schema for validation in app/services/lms/schemas.py (QuestionCreate, QuestionRead)

Validate on create: exactly 4 options, unique option texts, correct_option_index in range.

No HTTP routes yet. Match coding style of app/models/models.py accessors if needed.
""",
)

task(
    task_id="F-002b",
    slug="question-source-metadata",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=["F-002"],
    mvp=True,
    description="Track question origin: manual, pdf_qa_converted, pdf_ai, mixed.",
    objective="Enable audit trail and analytics by question source.",
    requirements=[
        "Add source_type enum column to questions",
        "Add source_pdf_thread_id (nullable FK/string matching rag_threads.thread_id)",
        "Add source_question_number, extraction_confidence (nullable float)",
    ],
    files_create=["docs/development-plan/migrations/002b_question_source.sql"],
    files_modify=["app/models/database_models.py", "app/services/lms/question_bank_service.py"],
    acceptance=[
        "Questions can be filtered by source_type",
        "PDF-derived questions link to rag thread id",
    ],
    constraints=["Backward compatible — existing questions default source_type='manual'"],
    cursor_prompt="""
Implement F-002b: Add question source metadata to the questions table.

Add columns:
- source_type: VARCHAR CHECK IN ('manual','pdf_qa_converted','pdf_ai','mixed') DEFAULT 'manual'
- source_pdf_thread_id: nullable string (rag thread)
- source_question_number: nullable int
- extraction_confidence: nullable float

Update Question model, migration SQL, and question_bank_service to accept/set these fields.
Update Pydantic schemas accordingly.
""",
)

task(
    task_id="F-002c",
    slug="mcq-option-schema",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=["F-002"],
    mvp=True,
    description="Formalize MCQ option storage with LaTeX support for math rendering.",
    objective="Ensure consistent 4-option MCQ structure across PDF pipeline and UI.",
    requirements=[
        "Options stored as JSON array of {label, text, latex?}",
        "Validator: exactly 4 options, unique text, valid correct_option_index",
        "Helper to render for API responses",
    ],
    files_create=["app/services/lms/mcq_utils.py"],
    files_modify=["app/services/lms/schemas.py", "app/services/lms/question_bank_service.py"],
    acceptance=[
        "mcq_utils.validate_mcq() rejects invalid MCQs",
        "API schema documents 4-option structure",
    ],
    constraints=["Compatible with MathJax frontend rendering"],
    cursor_prompt="""
Implement F-002c MCQ option schema helpers.

Create app/services/lms/mcq_utils.py:
- MCQOption pydantic model: label (A-D), text, latex optional
- validate_mcq(options: list, correct_index: int) -> raises ValueError with clear message
- normalize_options() — ensure labels A,B,C,D
- shuffle_options(correct_index) -> new index after shuffle

Integrate validation into question_bank_service create/update paths.
Update schemas.py with MCQOption and MCQQuestionCreate types.
""",
)

task(
    task_id="F-003",
    slug="class-enrollment-schema",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=[],
    mvp=True,
    description="Teacher-owned classes and student enrollments with join codes.",
    objective="Foundation for assignments and teacher dashboards.",
    requirements=[
        "classes table: teacher_id, name, description, join_code (unique), grade_level, is_active",
        "class_enrollments: class_id, student_id, enrolled_at, status",
        "Unique constraint on (class_id, student_id)",
    ],
    files_create=[
        "docs/development-plan/migrations/003_classes.sql",
        "app/services/lms/class_service.py",
    ],
    files_modify=["app/models/database_models.py", "app/utils/db.py"],
    acceptance=[
        "Teacher can create class via service",
        "Student can enroll by join_code via service",
        "List students in class, list classes for teacher/student",
    ],
    constraints=[
        "Do not confuse with User.class_standard field — that is grade label, not Class entity",
        "join_code: secure random 6-8 chars, unique",
    ],
    cursor_prompt="""
Implement F-003 Class and Enrollment schema for IqbalAI LMS.

Create:
1. Class and ClassEnrollment SQLAlchemy models
2. Migration 003_classes.sql
3. app/services/lms/class_service.py:
   - create_class(teacher_id, name, grade_level, ...) -> generates join_code
   - enroll_student(join_code, student_id)
   - list_teacher_classes(teacher_id)
   - list_class_students(class_id)
   - list_student_classes(student_id)

Use secrets/token for join_code generation. Add indexes on teacher_id, join_code.
No routes yet — service layer only with tests if feasible.
""",
)

task(
    task_id="F-004",
    slug="assessment-quiz-schema",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=["F-001", "F-002"],
    mvp=True,
    description="First-class assessments/quizzes (diagnostic and assignment quiz types).",
    objective="Decouple quizzes from lesson JSON content.",
    requirements=[
        "assessments table: title, type (diagnostic|quiz), creation_mode, created_by, status (draft|published|archived)",
        "assessment_questions join: assessment_id, question_id, sort_order",
        "Support time_limit_minutes nullable",
    ],
    files_create=[
        "docs/development-plan/migrations/004_assessments.sql",
        "app/services/lms/assessment_service.py",
    ],
    files_modify=["app/models/database_models.py"],
    acceptance=[
        "Create assessment with ordered question IDs",
        "Publish/unpublish assessment",
        "List assessments by teacher and type",
    ],
    constraints=["Do not break existing lesson assessment_quiz embedded in JSON"],
    cursor_prompt="""
Implement F-004 Assessment/Quiz schema.

Models:
- Assessment: id, title, description, assessment_type ('diagnostic'|'quiz'), creation_mode ('manual'|'pdf_qa_auto'|'pdf_ai'|'mixed')
  created_by FK, status, time_limit_minutes, created_at, updated_at
- AssessmentQuestion: assessment_id, question_id, sort_order — unique (assessment_id, question_id)

Service assessment_service.py:
- create_assessment, add_questions, remove_question, reorder, publish, get_with_questions

Migration 004_assessments.sql + model registration.
""",
)

task(
    task_id="F-004b",
    slug="pdf-source-link-schema",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=["F-004"],
    mvp=True,
    description="Link quizzes to PDF/RAG source and store extraction metadata.",
    objective="Traceability for PDF→MCQ pipeline.",
    requirements=[
        "quiz_pdf_sources table: assessment_id, rag_thread_id, original_filename, extraction_status, overall_confidence",
        "pdf_qa_extractions table: raw extraction JSON, paired count, warnings",
    ],
    files_create=["docs/development-plan/migrations/004b_pdf_sources.sql"],
    files_modify=["app/models/database_models.py", "app/services/lms/assessment_service.py"],
    acceptance=[
        "Assessment can be linked to RAG thread after PDF upload",
        "Extraction metadata persisted for audit",
    ],
    constraints=["Reuse rag_threads.thread_id as FK reference"],
    cursor_prompt="""
Implement F-004b PDF source link schema.

Create models:
- QuizPdfSource: assessment_id, rag_thread_id, original_filename, extraction_status (pending|processing|completed|failed), overall_confidence, error_message
- PdfQaExtraction: id, quiz_pdf_source_id, raw_extraction_json (Text), pair_count, warnings_json, created_at

Wire into assessment_service: link_pdf_source(assessment_id, thread_id, filename)
Migration 004b_pdf_sources.sql
""",
)

task(
    task_id="F-005",
    slug="submission-attempt-schema",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=["F-004"],
    mvp=True,
    description="Student assessment attempts and per-question answers.",
    objective="Enable scoring and performance analytics.",
    requirements=[
        "assessment_attempts: student_id, assessment_id, assignment_id (nullable), started_at, submitted_at, score, max_score, status",
        "attempt_answers: attempt_id, question_id, selected_option_index, is_correct",
    ],
    files_create=[
        "docs/development-plan/migrations/005_attempts.sql",
        "app/services/lms/attempt_service.py",
    ],
    files_modify=["app/models/database_models.py"],
    acceptance=[
        "Start attempt, save answers, submit attempt",
        "Calculate score from correct_option_index",
    ],
    constraints=["Prevent answer leakage in API — separate delivery vs grading"],
    cursor_prompt="""
Implement F-005 Submission and Attempt schema.

Models: AssessmentAttempt, AttemptAnswer
Service attempt_service.py:
- start_attempt(student_id, assessment_id, assignment_id=None)
- save_answer(attempt_id, question_id, selected_option_index)
- submit_attempt(attempt_id) -> computes score, marks completed
- get_attempt_results(attempt_id) for student/teacher

Migration 005_attempts.sql. Index on (student_id, assessment_id).
""",
)

task(
    task_id="F-006",
    slug="performance-mastery-schema",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=["F-001", "F-005"],
    mvp=True,
    description="Topic-level scores and mastery status per student.",
    objective="Foundation for weakness detection and learning paths.",
    requirements=[
        "student_topic_scores: student_id, topic_id, score_percent, mastery_status, last_assessed_at",
        "mastery_snapshots: student_id, snapshot_json, created_at (historical)",
        "mastery_status enum: mastered, improving, needs_practice, weak",
    ],
    files_create=[
        "docs/development-plan/migrations/006_mastery.sql",
        "app/services/lms/performance_service.py",
    ],
    files_modify=["app/models/database_models.py"],
    acceptance=[
        "Upsert topic score after assessment submit",
        "Query all topic scores for student",
    ],
    constraints=["Mastery thresholds configurable (default: >=85 mastered, <60 weak)"],
    cursor_prompt="""
Implement F-006 Performance and Mastery schema.

Models: StudentTopicScore, MasterySnapshot
performance_service.py:
- update_topic_scores_from_attempt(attempt_id) — aggregate by question topic
- get_student_mastery(student_id) -> list of topic scores + status
- compute_mastery_status(score_percent) using configurable thresholds
- create_snapshot(student_id) for history

Migration 006_mastery.sql
""",
)

task(
    task_id="F-007",
    slug="learning-path-schema",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P1",
    dependencies=["F-001", "F-006"],
    mvp=False,
    description="Personalized learning path sequences per student.",
    objective="Store ordered remediation steps.",
    requirements=[
        "learning_paths: student_id, title, status, created_at",
        "learning_path_items: path_id, item_type (lesson|quiz|practice), item_id, sort_order, status",
    ],
    files_create=[
        "docs/development-plan/migrations/007_learning_paths.sql",
        "app/services/lms/learning_path_service.py",
    ],
    files_modify=["app/models/database_models.py"],
    acceptance=["Create path, add items, mark item complete"],
    constraints=["Rule-based generation in P-402 — schema only here"],
    cursor_prompt="""
Implement F-007 Learning Path schema (data layer only).

Models: LearningPath, LearningPathItem
learning_path_service.py: CRUD for paths and items, mark_complete, get_current_item
Migration 007_learning_paths.sql
""",
)

task(
    task_id="F-008",
    slug="assignment-schema",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=["F-003", "F-004"],
    mvp=True,
    description="Assignments link ONE quiz to ONE class with due date. Quiz-only — no multi-item.",
    objective="Teacher assigns MCQ quiz to class.",
    requirements=[
        "assignments: teacher_id, class_id, quiz_id (FK assessments where type=quiz), title, due_date, status",
        "assignment_submissions: assignment_id, student_id, attempt_id (nullable), status, submitted_at",
    ],
    files_create=[
        "docs/development-plan/migrations/008_assignments.sql",
        "app/services/lms/assignment_service.py",
    ],
    files_modify=["app/models/database_models.py"],
    acceptance=[
        "Create assignment with quiz + class + due_date",
        "List assignments for class (teacher) and student",
        "Track submission status per student",
    ],
    constraints=["One quiz per assignment — no assignment_items multi-type table"],
    cursor_prompt="""
Implement F-008 Assignment schema — QUIZ ONLY.

Assignment model: teacher_id, class_id, quiz_id (FK assessments), title, instructions, due_date, status (draft|published|closed)
AssignmentSubmission: assignment_id, student_id, attempt_id nullable, status (not_started|in_progress|submitted|overdue)

assignment_service.py:
- create_assignment(teacher_id, class_id, quiz_id, due_date, ...)
- publish_assignment, list_for_class, list_for_student
- link_attempt_to_submission(assignment_id, student_id, attempt_id)

Migration 008_assignments.sql
""",
)

task(
    task_id="F-009",
    slug="db-migrations",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Database",
    priority="P0 BLOCKING",
    dependencies=["F-001", "F-008"],
    mvp=True,
    description="Consolidate and wire all LMS migrations into init_db.",
    objective="Reliable schema deployment without Alembic.",
    requirements=[
        "Single orchestrator script or init_db hook to run migrations in order",
        "Idempotent CREATE TABLE IF NOT EXISTS",
        "Dev seed: topics + optional sample questions",
    ],
    files_create=[
        "scripts/run_lms_migrations.py",
        "docs/development-plan/migrations/README.md",
    ],
    files_modify=["app/utils/db.py"],
    acceptance=[
        "Fresh DB gets all LMS tables",
        "Re-running migrations is safe",
    ],
    constraints=["Do not drop existing tables"],
    cursor_prompt="""
Implement F-009: Consolidate LMS migrations.

1. Ensure all migration SQL files in docs/development-plan/migrations/ are ordered 001-008
2. Create scripts/run_lms_migrations.py to execute SQL files against DATABASE_URL
3. Update init_db() to call LMS migration runner after existing table creation
4. Write migrations/README.md documenting process

Test on SQLite dev DB. Must be idempotent.
""",
)

task(
    task_id="F-010",
    slug="rbac-extension",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Backend",
    priority="P0 BLOCKING",
    dependencies=["F-003"],
    mvp=True,
    description="Extend RBAC for LMS permissions.",
    objective="Secure class, quiz, assignment, and performance endpoints.",
    requirements=[
        "Add permissions: MANAGE_CLASS, CREATE_QUIZ, CREATE_DIAGNOSTIC, ASSIGN_QUIZ, VIEW_CLASS_PERFORMANCE, MANAGE_QUESTION_BANK",
        "Map to teacher and admin roles",
        "Helper: teacher_owns_class(user_id, class_id)",
    ],
    files_create=["app/rbac/lms_permissions.py"],
    files_modify=["app/rbac/permissions.py", "app/rbac/roles.py"],
    acceptance=[
        "Teachers have LMS permissions; students do not",
        "Decorators can guard LMS routes",
    ],
    constraints=["Follow existing RBAC pattern in app/rbac/"],
    cursor_prompt="""
Extend RBAC for LMS (F-010).

Add to app/rbac/permissions.py:
MANAGE_CLASS, CREATE_QUIZ, CREATE_DIAGNOSTIC, ASSIGN_QUIZ, VIEW_CLASS_PERFORMANCE, MANAGE_QUESTION_BANK

Assign to Role.TEACHER and Role.ADMIN appropriately.
Create app/rbac/lms_permissions.py with:
- teacher_owns_class(user_id, class_id) -> bool
- student_in_class(user_id, class_id) -> bool

Add @permission_required decorator usage examples in docstring.
Update app/rbac/README.md
""",
)

task(
    task_id="F-011",
    slug="service-layer-scaffolding",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Backend",
    priority="P0 BLOCKING",
    dependencies=["F-009"],
    mvp=True,
    description="Organize LMS services under app/services/lms/ and quiz under app/services/quiz/.",
    objective="Clean architecture before routes.",
    requirements=[
        "Package structure with __init__.py exports",
        "Consistent error types LMSNotFoundError, LMSValidationError",
        "Logging pattern matching existing services",
    ],
    files_create=[
        "app/services/lms/__init__.py",
        "app/services/lms/exceptions.py",
        "app/services/quiz/__init__.py",
    ],
    files_modify=[],
    acceptance=[
        "All F-001–F-008 services importable from packages",
        "Exceptions used consistently",
    ],
    constraints=["No circular imports with routes"],
    cursor_prompt="""
Implement F-011 service layer scaffolding.

Ensure packages:
- app/services/lms/ (curriculum, question_bank, class, assessment, attempt, performance, assignment, learning_path services)
- app/services/quiz/ (models.py placeholder for A-325)

Create exceptions.py: LMSNotFoundError, LMSValidationError, LMSPermissionError
Update __init__.py to export public service functions.

Verify all services import without circular dependency errors.
""",
)

task(
    task_id="F-012",
    slug="lms-api-blueprint",
    phase="Phase 1 — Foundation",
    phase_folder="phase-1-foundation",
    task_type="Backend",
    priority="P1",
    dependencies=["F-011"],
    mvp=False,
    description="Register /api/lms blueprint with standard JSON responses.",
    objective="HTTP layer for all LMS features.",
    requirements=[
        "Blueprint app/routes/lms_routes.py registered in app/__init__.py",
        "Standard response envelope: {success, data, error}",
        "login_required on all routes; RBAC on mutations",
    ],
    files_create=["app/routes/lms_routes.py", "app/utils/lms_api.py"],
    files_modify=["app/__init__.py"],
    acceptance=[
        "Blueprint registered; /api/lms/health returns 200",
        "Helper json_success/json_error used",
    ],
    constraints=["Prefix /api/lms — do not conflict with /api/lessons"],
    cursor_prompt="""
Implement F-012 LMS API blueprint scaffold.

Create app/routes/lms_routes.py with bp = Blueprint('lms', __name__, url_prefix='/api/lms')
Create app/utils/lms_api.py: json_success(data, status=200), json_error(message, code, status=400)

Register blueprint in app/__init__.py
Add GET /api/lms/health (login optional) and stub route structure comments for future tasks.

Follow patterns from app/routes/lesson_routes.py for auth decorators.
""",
)

# Continue with remaining tasks - I'll add them in batches in the script
# Phase 2
for tid, slug, title, desc, obj, deps, mvp, req, create, modify, acc, con, prompt in [
    ("S-201", "student-onboarding-gate", "Student Onboarding Gate",
     "Redirect new students to diagnostic; show pending assignments on dashboard.",
     "Gate student experience until diagnostic complete; surface assignments.",
     ["F-005", "F-008", "S-205"], True,
     ["Add diagnostic_completed flag on student profile or user metadata", "Post-login redirect logic in chat routes or auth", "API: GET /api/lms/students/me/onboarding-status"],
     ["app/services/lms/student_profile_service.py"],
     ["app/routes/chat.py", "templates/student_dashboard/student_dashboard.html"],
     ["New student redirected to diagnostic", "Completed student sees dashboard normally", "Pending assignments visible"],
     ["Do not break existing student dashboard routes"],
     """Implement S-201 Student onboarding gate.

Add student profile fields: diagnostic_completed (bool), diagnostic_assessment_id (nullable).
student_profile_service.py: get_onboarding_status(student_id), mark_diagnostic_complete(student_id)

Backend: check after login — if student and not diagnostic_completed, frontend receives flag.
Update student dashboard JS to redirect to diagnostic flow when flag set.
Add /api/lms/students/me/onboarding-status endpoint in lms_routes.py.
"""),
    ("S-202", "student-home-dashboard", "Student Home Dashboard",
     "Dashboard section: next step, weak topics, pending quizzes, progress.",
     "Replace static recommendation cards with real LMS data.",
     ["S-201", "A-310"], True,
     ["Fetch progress, assignments, learning path in one dashboard API", "UI cards for weak topics and pending quizzes"],
     [],
     ["templates/student_dashboard/student_dashboard.html"],
     ["Dashboard shows real weak topics from API", "Pending assignments listed", "Progress percentage shown"],
     ["Incremental change to monolithic template — use new section IDs"],
     """Implement S-202 Student home dashboard LMS section.

Create GET /api/lms/students/me/dashboard aggregating: onboarding, progress (A-310), pending assignments, learning path current step.

Update student_dashboard.html: add 'LMS Overview' section at top with cards for Weak Topics, Pending Quizzes, Overall Progress.
Remove or demote static generateTeachingRecommendations for this section only.
Use fetch + render pattern already in template.
"""),
    ("S-203", "topic-content-browse", "Topic-Based Content Browse",
     "Filter public lessons by taxonomy topic.",
     "Connect curriculum to existing lesson browse.",
     ["F-001"], False,
     ["lesson_topics join table", "API filter on browse_lessons by topic_id"],
     ["docs/development-plan/migrations/009_lesson_topics.sql"],
     ["app/routes/lesson_routes.py"],
     ["Lessons filterable by topic slug", "UI topic dropdown on student browse"],
     ["Optional if lesson_topics empty — show all"],
     """Implement S-203 Topic-based lesson browse.

Create lesson_topics (lesson_id, topic_id) join table.
Extend browse_lessons API with optional topic_id or topic_slug query param.
Add topic filter dropdown to student lesson browse UI.
"""),
    ("S-204", "link-lessons-to-topics", "Link Lessons to Topics",
     "Map lessons.focus_area to taxonomy topics.",
     "Enable learning path to reference lessons by topic.",
     ["F-001"], False,
     ["Backfill script matching focus_area strings to topic names", "Admin/teacher optional topic tags on lesson"],
     ["scripts/backfill_lesson_topics.py"],
     ["app/models/database_models.py"],
     ["Lessons linked to at least one topic where focus_area matches"],
     ["Fuzzy match focus_area to topic name — log unmatched"],
     """Implement S-204 Link lessons to topics.

Script backfill_lesson_topics.py: for each lesson with focus_area, find topic by similar name, insert lesson_topics.
Add optional topic_ids to lesson update API (teacher only).
"""),
    ("S-205", "class-enrollment-ui", "Class Enrollment UI",
     "Student joins class via join code; teacher sees roster.",
     "Complete class enrollment user flow.",
     ["F-003", "F-010"], True,
     ["Student: enter join code modal", "Teacher: display join code on class page", "APIs wired to class_service"],
     [],
     ["templates/student_dashboard/student_dashboard.html", "templates/teacher_dashboard.html"],
     ["Student successfully joins class with valid code", "Invalid code shows error", "Teacher sees updated roster"],
     ["Reuse existing dashboard modal patterns"],
     """Implement S-205 Class enrollment UI.

API endpoints (lms_routes): POST /api/lms/classes/join {join_code}, GET /api/lms/classes/mine
Teacher UI: Create Class modal, show join_code, student list.
Student UI: Join Class modal with code input.
Use class_service from F-003. RBAC: TE-603.
"""),
    ("S-206", "student-profile-extensions", "Student Profile Extensions",
     "Extended student metadata for LMS state.",
     "Centralize student LMS state.",
     ["F-006", "S-201"], False,
     ["student_profiles table or extend users with JSON metadata", "current_learning_path_id, enrolled_at tracking"],
     ["docs/development-plan/migrations/010_student_profiles.sql"],
     ["app/models/database_models.py"],
     ["Profile stores diagnostic and path state"],
     ["Prefer separate table over altering users heavily"],
     """Implement S-206 Student profile extensions.

Create student_profiles: user_id PK, diagnostic_completed, diagnostic_completed_at, current_learning_path_id, created_at, updated_at
student_profile_service CRUD. Link from S-201 onboarding gate.
"""),
]:
    task(
        task_id=tid, slug=slug, title=title, phase="Phase 2 — Student Core",
        phase_folder="phase-2-student-core", task_type="Backend + Frontend",
        priority="P0" if mvp else "P1", dependencies=deps, mvp=mvp,
        description=desc, objective=obj, requirements=req,
        files_create=create, files_modify=modify, acceptance=acc, constraints=con,
        cursor_prompt=prompt,
    )

# Phase 3 - PDF pipeline (critical)
PHASE3_PDF = [
    ("A-325", "pydantic-extraction-models", "Pydantic Extraction Models", "P0 BLOCKING", [], True,
     "Define all Pydantic models for PDF extraction and MCQ output.",
     "Create app/services/quiz/models.py with ExtractedQuestion, ExtractedAnswer, PDFExtractionResult, QuestionAnswerPair, MCQOption, MCQQuestion, MCQBatchResult and validators (4 unique options).",
     ["app/services/quiz/models.py"],
     [],
     ["All models validate correctly in unit tests", "MCQQuestion rejects !=4 options"],
     ["Use Pydantic v2 model_validator", "Follow app/services/lesson/models.py style"],
     """Implement A-325 Pydantic models for PDF→MCQ pipeline.

Create app/services/quiz/models.py with:
- ExtractedQuestion, ExtractedAnswer (flexible number: int|str)
- PDFExtractionResult (title, questions, answers, format_detected, confidence, warnings)
- QuestionAnswerPair (match_confidence, is_matched)
- MCQOption (label A-D, text, latex optional)
- MCQQuestion with validator: exactly 4 unique options, correct_option_label valid
- MCQBatchResult (quiz_title, questions, failed_conversions)

Add tests in tests/test_quiz_models.py
"""),
    ("A-326", "ai-pdf-structured-extractor", "AI PDF Structured Extractor", "P0 BLOCKING", ["A-325", "RAG ingest"], True,
     "LangChain with_structured_output to extract Q&A from variable PDF formats.",
     "Intelligent extraction — not regex-only.",
     ["app/services/quiz/pdf_extractor.py", "Uses LLM gateway from app/utils/llm_gateway.py"],
     ["app/services/quiz/pdf_extractor.py"],
     ["app/utils/llm_gateway.py"],
     ["Returns valid PDFExtractionResult from sample PDF text", "Handles Questions/Answers sections and inline formats"],
     ["Do not invent answers — only extract from document", "Use structured output"],
     """Implement A-326 AI PDF structured extractor.

Create pdf_extractor.py:
- extract_qa_from_text(pdf_text: str) -> PDFExtractionResult
- Use LangChain with_structured_output(PDFExtractionResult)
- Prompt: extract all Q&A pairs, format may vary, do not invent answers
- Integrate with existing LLM provider config (Groq/OpenAI via llm_gateway)

Add tests with fixture text mimicking Post Test PDF (quadratic equations).
"""),
    ("A-327", "intelligent-qa-pairing", "Intelligent Q↔A Pairing", "P0 BLOCKING", ["A-326"], True,
     "Pair questions to answers with confidence scores.",
     "Handle varied numbering and layouts.",
     ["pair_questions_answers(extraction: PDFExtractionResult) -> list[QuestionAnswerPair]"],
     ["app/services/quiz/pdf_extractor.py"],
     [],
     ["All matched pairs have is_matched=True", "Unmatched items flagged in warnings"],
     ["match_confidence 0-1"],
     """Implement A-327 Q↔A pairing in pdf_extractor.py.

Function pair_questions_answers():
- Match by normalized question number
- Fallback: LLM-assisted pairing for unmatched with structured QuestionAnswerPair output
- Set match_confidence and is_matched=False when no answer found
"""),
    ("A-328", "ai-mcq-converter", "AI MCQ Converter", "P0 BLOCKING", ["A-327", "A-325"], True,
     "Convert Q+A pair to 4-option MCQ; PDF answer is correct; AI generates 3 distractors.",
     "Core MCQ generation for assignment quizzes.",
     ["app/services/quiz/mcq_converter.py"],
     [],
     ["Output passes MCQQuestion validation", "Correct answer from PDF always in options"],
     ["Shuffle option positions", "Math notation preserved in latex fields"],
     """Implement A-328 AI MCQ converter.

mcq_converter.py:
- convert_pair_to_mcq(pair: QuestionAnswerPair) -> MCQQuestion
- LLM generates 3 plausible distractors for math (common mistakes)
- PDF answer must be one of 4 options; shuffle positions; track correct_option_label
- Use LangChain with_structured_output(MCQQuestion)

Prompt: distractors must be plausible wrong answers, unique, no 'all of the above'.
Math: preserve notation in latex fields for MathJax UI.
"""),
    ("A-328b", "validation-retry-loop", "Validation Retry Loop", "P0", ["A-328"], True,
     "Retry AI on Pydantic validation failure (max 2).",
     "Robust pipeline.",
     ["app/services/quiz/retry_utils.py"],
     ["app/services/quiz/mcq_converter.py"],
     ["Invalid MCQ triggers retry with error message in prompt", "Fails gracefully after max retries"],
     ["Max 2 retries"],
     """Implement A-328b validation retry loop.

Wrap convert_pair_to_mcq with retry_on_validation_error(max_retries=2).
Pass ValidationError details back to LLM on retry.
Log failures to failed_conversions list in MCQBatchResult.
"""),
    ("A-329", "conversion-orchestrator", "Conversion Orchestrator", "P0 BLOCKING", ["A-326", "A-328b", "F-002"], True,
     "Celery pipeline: PDF upload → ingest → extract → convert → save DB.",
     "End-to-end PDF to quiz questions in database.",
     ["app/services/quiz/pipeline.py", "app/tasks/quiz_pdf_tasks.py"],
     ["app/celery_app.py"],
     ["Pipeline saves questions to question bank and links assessment", "Status updates on QuizPdfSource"],
     ["Reuse existing Celery ingest where possible"],
     """Implement A-329 PDF→MCQ orchestrator pipeline.

pipeline.py:
- run_pdf_quiz_pipeline(assessment_id, rag_thread_id, pdf_text)
- Steps: extract -> pair -> convert each -> save via question_bank_service -> add to assessment
- Update QuizPdfSource extraction_status and overall_confidence

Celery task quiz_pdf_tasks.process_pdf_quiz.delay(assessment_id, thread_id)
Hook after RAG ingest completes or accept pre-extracted text from rag chunks.
"""),
    ("A-333", "confidence-gating", "Confidence Gating", "P0", ["A-327"], True,
     "Block publish if confidence <0.60; flag 0.60-0.84 for review.",
     "Quality gate before teacher publish.",
     ["app/services/lms/assessment_service.py"],
     [],
     ["Publish blocked when overall_confidence < 0.60", "Warnings returned for 0.60-0.84"],
     ["API returns clear error message to teacher"],
     """Implement A-333 confidence gating.

In assessment_service.publish():
- Compute overall_confidence from QuizPdfSource and pair match_confidences
- < 0.60: raise LMSValidationError('Review required — confidence too low')
- 0.60-0.84: allow publish but set requires_review flag
- >= 0.85: normal publish

Expose confidence in GET assessment API.
"""),
]

for t in PHASE3_PDF:
    task(
        task_id=t[0], slug=t[1], title=t[2], phase="Phase 3 — Quiz / Assessment / PDF",
        phase_folder="phase-3-quiz-assessment-pdf", task_type="AI + Backend",
        priority=t[3], dependencies=t[4], mvp=t[5], description=t[6], objective=t[7],
        requirements=[t[6], t[7]] if isinstance(t[6], str) else t[6],
        files_create=t[8], files_modify=t[9], acceptance=t[10], constraints=t[11],
        cursor_prompt=t[12],
    )

# More phase 3 tasks - abbreviated generation with full prompts
PHASE3_REST = [
    ("A-301", "question-bank-crud-api", "Question Bank CRUD API", "Backend", "P1", ["F-002", "F-010"], False,
     "REST API for manual question management.", "Expose question_bank_service via /api/lms/questions",
     ["CRUD endpoints with RBAC MANAGE_QUESTION_BANK", "Pagination and topic filter"],
     ["app/routes/lms_routes.py"], ["app/services/lms/question_bank_service.py"],
     ["Teachers can create/edit/delete questions via API"],
     ["Validate MCQ on create"],
     "Implement A-301 Question Bank CRUD API in lms_routes.py: GET/POST /questions, GET/PUT/DELETE /questions/<id>. Teacher+admin only. Pagination, filter by topic_id."),
    ("A-302", "quiz-builder-api", "Quiz Builder API", "Backend", "P0", ["F-004", "A-301"], True,
     "API to create quizzes from question IDs.", "Build and publish assessments.",
     ["POST /api/lms/quizzes", "Add/remove/reorder questions", "Publish endpoint"],
     ["app/routes/lms_routes.py"], ["app/services/lms/assessment_service.py"],
     ["Create quiz, add questions, publish"],
     ["assessment_type=quiz for assignments"],
     "Implement A-302 Quiz Builder API: POST /quizzes, PUT /quizzes/<id>/questions, POST /quizzes/<id>/publish. Use assessment_service."),
    ("A-303", "quiz-delivery-api", "Quiz Delivery API", "Backend", "P0 BLOCKING", ["F-004", "A-302"], True,
     "Deliver MCQs to students without leaking answers.", "Secure quiz taking.",
     ["GET questions without correct_index", "Start attempt session"],
     ["app/routes/lms_routes.py"], ["app/services/lms/attempt_service.py"],
     ["Student receives questions without answers", "Attempt tracked"],
     ["Never expose correct_option_index in delivery API"],
     "Implement A-303 Quiz delivery: POST /quizzes/<id>/start, GET /attempts/<id>/questions (strip answers), POST /attempts/<id>/answer. Student only."),
    ("A-304", "submission-scoring-engine", "Submission & Scoring Engine", "Backend", "P0 BLOCKING", ["F-005", "A-303"], True,
     "Score attempts and trigger mastery update.", "Complete assessment loop.",
     ["POST submit computes score", "Calls performance_service.update_topic_scores_from_attempt"],
     ["app/routes/lms_routes.py"], ["app/services/lms/attempt_service.py", "app/services/lms/performance_service.py"],
     ["Score calculated correctly", "Topic scores updated"],
     ["Idempotent submit"],
     "Implement A-304: POST /attempts/<id>/submit — grade answers, set score, call performance_service, return results with topic breakdown."),
    ("A-305", "platform-default-diagnostic", "Platform Default Diagnostic", "Database", "P1", ["A-302"], False,
     "Seed default onboarding diagnostic.", "Baseline for all students.",
     ["Seed script or admin-created diagnostic quiz"],
     ["scripts/seed_default_diagnostic.py"], [],
     ["Default diagnostic exists for Math"],
     ["Can use manual questions initially"],
     "Implement A-305: seed_default_diagnostic.py creating a published diagnostic assessment with sample MCQs for Math topics."),
    ("A-306", "student-quiz-ui", "Student Quiz UI", "Frontend", "P0", ["A-303"], True,
     "MCQ quiz taking interface with MathJax.", "Student completes quizzes.",
     ["Quiz modal or page", "One question at a time or paginated", "Submit flow"],
     ["templates/student_dashboard/lms_quiz.html or section in student_dashboard.html"], [],
     ["Student can complete quiz end-to-end", "Math renders via MathJax"],
     ["Mobile responsive"],
     "Implement A-306 Student quiz UI: MCQ interface calling A-303 APIs, MathJax for latex, progress indicator, submit confirmation."),
    ("A-307", "diagnostic-results-ui", "Diagnostic Results UI", "Frontend", "P0", ["A-304", "F-006"], True,
     "Show topic scores after diagnostic.", "Student sees strengths/weaknesses.",
     ["Bar/list of topic scores", "Strong/weak labels"],
     [], ["templates/student_dashboard/student_dashboard.html"],
     ["Shows Algebra 90%, Fractions 55% style results"],
     ["Call onboarding complete after viewing"],
     "Implement A-307 Diagnostic results UI after submit: topic breakdown chart/list, mark diagnostic complete via S-201 API."),
    ("A-308", "weakness-detection-service", "Weakness Detection Service", "Backend", "P0 BLOCKING", ["A-304", "F-006"], True,
     "Identify weak/strong topics from scores.", "Drive learning paths.",
     ["analyze_diagnostic(attempt_id)", "Thresholds: weak <60%, strong >=80%"],
     ["app/services/lms/performance_service.py"], [],
     ["Returns weak_topics and strong_topics lists"],
     ["Configurable thresholds"],
     "Implement A-308 in performance_service: analyze_attempt(attempt_id) -> weak_topics, strong_topics using configurable thresholds."),
    ("A-309", "mastery-calculation", "Mastery Calculation", "Backend", "P0", ["A-308"], True,
     "Compute mastery status labels.", "Progress tracking.",
     ["mastered/improving/needs_practice/weak"],
     ["app/services/lms/performance_service.py"], [],
     ["Correct status for sample scores"],
     [],
     "Implement A-309 mastery status rules in performance_service.compute_mastery_status(score, previous_score optional)."),
    ("A-310", "student-progress-api", "Student Progress API", "Backend", "P0", ["A-309"], True,
     "API for student mastery overview.", "Dashboard data.",
     ["GET /api/lms/students/me/progress"],
     ["app/routes/lms_routes.py"], [],
     ["Returns all topic scores and overall percent"],
     [],
     "Implement A-310 GET /api/lms/students/me/progress returning topic mastery list and overall progress percentage."),
    ("A-311", "student-progress-ui", "Student Progress UI", "Frontend", "P0", ["A-310"], True,
     "Visual progress dashboard.", "Student tracks mastery.",
     ["Progress section in student dashboard"],
     [], ["templates/student_dashboard/student_dashboard.html"],
     ["Shows mastery labels per topic"],
     [],
     "Implement A-311 Student progress UI section consuming A-310 API with color-coded mastery badges."),
    ("A-330", "pdf-qa-upload-ui", "PDF Q&A Upload UI", "Frontend", "P0", ["A-329"], True,
     "Teacher uploads Q&A PDF for quiz generation.", "Entry point for PDF pipeline.",
     ["Upload component", "Processing status polling"],
     [], ["templates/teacher_dashboard.html"],
     ["Upload triggers pipeline", "Progress shown"],
     ["Reuse existing PDF upload patterns"],
     "Implement A-330 Teacher PDF Q&A upload UI in quiz creation hub: file upload, link to assessment draft, poll extraction status."),
    ("A-331", "mcq-preview-edit-ui", "MCQ Preview & Edit UI", "Frontend", "P0", ["A-328", "A-333"], True,
     "Review/edit generated MCQs before publish.", "Teacher quality control.",
     ["List all MCQs", "Edit options", "Regenerate distractors button"],
     [], ["templates/teacher_dashboard.html"],
     ["Teacher can edit and save MCQs"],
     ["Mandatory review for confidence <0.85 recommended"],
     "Implement A-331 MCQ preview/edit UI: show generated questions, inline edit, regenerate single question via API, save to question bank."),
    ("A-332", "auto-quiz-creation", "Auto Quiz Creation", "Backend", "P0", ["A-329", "A-302"], True,
     "Auto-create quiz record from pipeline output.", "Bridge pipeline to assignment.",
     ["POST finalize creates published-ready quiz"],
     ["app/routes/lms_routes.py"], ["app/services/quiz/pipeline.py"],
     ["Quiz created with all pipeline questions attached"],
     [],
     "Implement A-332: POST /api/lms/quizzes/from-pdf/<source_id>/finalize — attach all converted questions, set creation_mode=pdf_qa_auto."),
]

for t in PHASE3_REST:
    task(
        task_id=t[0], slug=t[1], title=t[2], phase="Phase 3 — Quiz / Assessment / PDF",
        phase_folder="phase-3-quiz-assessment-pdf", task_type=t[3],
        priority=t[4], dependencies=t[5], mvp=t[6], description=t[7], objective=t[8],
        requirements=t[9], files_create=t[10], files_modify=t[11], acceptance=t[12],
        constraints=t[13], cursor_prompt=t[14],
    )

# Phase 3 optional tasks
for tid, slug, title, pri, deps, mvp, prompt in [
    ("A-301b", "question-bank-ui", "Question Bank UI", "P1", ["A-301"], False,
     "Implement A-301b Manual Question Bank UI for teachers: table of questions, create/edit form with 4 options, topic selector."),
    ("A-315", "pdf-diagnostic-upload", "PDF Diagnostic Upload", "P1", ["F-004b", "RAG"], False,
     "Implement A-315 Diagnostic PDF upload API — reuse RAG ingest, create assessment type=diagnostic, return thread_id for topic selection."),
    ("A-316", "pdf-topic-listing", "PDF Topic Listing API", "P1", ["A-315"], False,
     "Implement A-316 GET /api/lms/diagnostics/pdf/<thread_id>/topics — list RAGHeading topics from uploaded PDF."),
    ("A-317", "diagnostic-topic-selection-ui", "Diagnostic Topic Selection UI", "P1", ["A-316"], False,
     "Implement A-317 UI for teacher to select PDF topics and set question count per topic for diagnostic generation."),
    ("A-318", "ai-diagnostic-generator", "AI Diagnostic Question Generator", "P1", ["A-316"], False,
     "Implement A-318 Generate diagnostic MCQs from PDF chunks + selected topics (non Q&A PDF format) using structured MCQQuestion output."),
    ("A-319", "generated-question-review-ui", "Generated Question Review UI", "P0", ["A-331"], True,
     "Implement A-319 Shared review UI for AI-generated diagnostic questions — reuse A-331 components."),
    ("A-321", "mixed-quiz-composer", "Mixed Quiz Composer", "P2", ["A-301", "A-328"], False,
     "Implement A-321 Mixed quiz: combine manual question bank picks + PDF-generated questions into one assessment."),
    ("A-313", "assessment-history", "Assessment History", "P1", ["A-304"], False,
     "Implement A-313 GET /api/lms/students/me/attempts history with scores and dates; UI list on student dashboard."),
]:
    task(task_id=tid, slug=slug, title=title, phase="Phase 3 — Quiz / Assessment / PDF",
         phase_folder="phase-3-quiz-assessment-pdf", task_type="Backend/Frontend",
         priority=pri, dependencies=deps, mvp=mvp, description=title, objective=title,
         requirements=[prompt.split('\n')[0]], files_create=[], files_modify=["app/routes/lms_routes.py"],
         acceptance=[f"{tid} complete"], constraints=["Follow existing patterns"],
         cursor_prompt=prompt)

# Phases 4-10 - generate with structured prompts
PHASES_4_10 = {
    "phase-4-personalization": ("Phase 4 — Personalization", [
        ("P-401", "learning-path-templates", "Learning Path Templates", "P1", ["F-007"], False, "Create learning_path_templates and template_items tables + seed remediation templates for weak topics."),
        ("P-402", "rule-based-path-generator", "Rule-Based Path Generator", "P0", ["A-308", "P-401"], True, "Implement rule-based learning path generation from weak_topics: ordered list of lessons/quizzes/reassessment. MVP deterministic rules."),
        ("P-403", "learning-path-api", "Learning Path API", "P0", ["P-402"], True, "GET/PUT /api/lms/students/me/learning-path — get path, mark item complete."),
        ("P-404", "learning-path-ui", "Learning Path UI", "P0", ["P-403"], True, "Student dashboard learning path widget: ordered steps, highlight current, completion checkmarks."),
        ("P-405", "path-refresh-reassessment", "Path Refresh on Reassessment", "P1", ["P-402", "A-304"], False, "Hook after assessment submit to regenerate/update learning path if weak topics changed."),
        ("P-406", "difficulty-tagging", "Difficulty Tagging", "P1", ["F-002"], False, "Ensure difficulty field on questions enforced in UI and API filters."),
        ("P-407", "adaptive-difficulty-engine", "Adaptive Difficulty Engine", "P2", ["A-309", "P-406"], False, "Select next question difficulty based on recent attempt performance — do not implement before mastery data exists."),
        ("P-408", "prerequisite-routing", "Prerequisite Routing", "P2", ["F-001", "P-407"], False, "When struggling, insert prerequisite topics into learning path using topic_prerequisites."),
        ("P-409", "ai-enhanced-path-generator", "AI-Enhanced Path Generator", "P3", ["P-402"], False, "Optional LLM layer to suggest path ordering and explanations beyond rules."),
    ]),
    "phase-5-ai-tutor-practice": ("Phase 5 — AI Tutor + Guided Practice", [
        ("T-501", "tutor-mode-system-prompt", "Tutor Mode System Prompt", "P1", [], True, "Add Socratic tutor system prompt to prompt_service.py: hints, step-by-step, avoid giving final answer when inappropriate for learning."),
        ("T-502", "practice-aware-tutor-context", "Practice-Aware Tutor Context", "P1", ["T-501", "A-304"], False, "Build context payload: current question, topic, attempt history, mastery for tutor calls."),
        ("T-503", "student-tutor-api", "Student Tutor API", "P1", ["T-502"], False, "POST /api/lms/tutor/chat — extend or wrap existing /chat with tutor mode and LMS context."),
        ("T-504", "student-tutor-ui", "Student Tutor UI", "P1", ["T-503"], False, "Ask Tutor panel in quiz/practice views."),
        ("T-505", "guided-practice-session-model", "Guided Practice Session Model", "P2", ["F-005"], False, "practice_sessions and practice_attempts tables for hint/retry tracking."),
        ("T-506", "practice-flow-api", "Practice Flow API", "P2", ["T-505"], False, "Practice session API: question → submit → hint → retry → next."),
        ("T-507", "guided-practice-ui", "Guided Practice UI", "P2", ["T-506"], False, "Interactive guided practice UI component."),
        ("T-508", "hint-escalation-logic", "Hint Escalation Logic", "P2", ["T-501"], False, "Progressive hints: nudge → strategy → partial step."),
        ("T-509", "align-lesson-qa-tutor", "Align Lesson Q&A Tutor", "P2", ["T-501"], False, "Apply tutor prompt to lesson_qa_graph.py for consistent behavior."),
        ("T-510", "pdf-context-for-tutor", "PDF Context for Tutor", "P2", ["A-332", "T-502"], False, "When quiz is PDF-sourced, include relevant PDF chunks in tutor context."),
    ]),
    "phase-6-teacher-platform": ("Phase 6 — Teacher Platform", [
        ("TE-601", "class-crud-api", "Class CRUD API", "P0", ["F-003", "F-010"], True, "Implement /api/lms/classes CRUD — create, list, update, archive. Teacher scoped."),
        ("TE-602", "class-management-ui", "Class Management UI", "P0", ["TE-601"], True, "Teacher dashboard class management section: create class, list classes, show join code."),
        ("TE-603", "student-enrollment", "Student Enrollment", "P0", ["TE-601"], True, "Enrollment management: add student, bulk import optional, join code display."),
        ("TE-612", "quiz-creation-hub-ui", "Quiz Creation Hub UI", "P0", ["A-330", "A-331"], True, "Teacher Quiz Creation Hub with tabs: PDF Q&A Auto (primary), Manual, Diagnostic PDF+Topics."),
        ("TE-613", "create-assignment", "Create Assignment", "P0", ["F-008", "A-332", "TE-601"], True, "Create Assignment flow: select/create quiz ONLY → pick class → due date → publish. One quiz per assignment."),
        ("TE-607", "submission-viewer-api", "Submission Viewer API", "P0", ["A-304", "TE-603"], True, "GET /api/lms/classes/<id>/assignments/<aid>/submissions — student scores and status."),
        ("TE-608", "class-roster-api", "Class Roster + Summary API", "P0", ["TE-603", "A-310"], True, "GET /api/lms/classes/<id>/students with summary metrics per student."),
        ("TE-609", "teacher-student-dashboard-ui", "Teacher Student Dashboard UI", "P0", ["TE-608"], True, "Per-student performance view: overall %, topic breakdown, struggling flags."),
        ("TE-610", "class-quiz-results-ui", "Class Quiz Results UI", "P0", ["TE-607"], True, "Class-level assignment results: completion %, avg score, overdue list."),
        ("TE-611", "enhanced-faq-insights", "Enhanced FAQ Insights", "P2", ["F-001"], False, "Bridge lesson FAQ to topic tags for class-level struggling insights."),
    ]),
    "phase-7-analytics": ("Phase 7 — Analytics", [
        ("AN-701", "class-topic-aggregation", "Class Topic Aggregation", "P2", ["A-310", "TE-603"], False, "Aggregate topic scores across class — e.g. 18/30 struggle with Fraction Word Problems."),
        ("AN-702", "learning-insights-ui", "Learning Insights UI", "P2", ["AN-701"], False, "Teacher dashboard insights cards with real aggregated data."),
        ("AN-703", "individual-student-report", "Individual Student Report", "P2", ["A-310"], False, "Generate PDF/HTML student performance report."),
        ("AN-704", "class-performance-report", "Class Performance Report", "P2", ["AN-701"], False, "Class-wide topic mastery report."),
        ("AN-705", "progress-over-time-charts", "Progress Over Time Charts", "P2", ["F-006"], False, "Chart mastery snapshots over time."),
        ("AN-706", "struggling-student-alerts", "Struggling Student Alerts", "P2", ["A-309"], False, "Flag students below threshold per topic/class."),
        ("AN-707", "export-csv-pdf", "Export CSV/PDF", "P3", ["AN-703"], False, "Export gradebook and reports."),
        ("AN-708", "pdf-source-analytics", "PDF Source Analytics", "P3", ["F-002b"], False, "Analytics comparing manual vs PDF-converted quiz effectiveness."),
    ]),
    "phase-8-interventions": ("Phase 8 — AI Recommendations", [
        ("R-801", "rule-based-intervention-engine", "Rule-Based Intervention Engine", "P2", ["A-308", "AN-701"], False, "Recommend actions for struggling students: extra quiz, review topic, reassess."),
        ("R-802", "intervention-api", "Intervention API", "P2", ["R-801"], False, "GET /api/lms/interventions?student_id=&topic_id="),
        ("R-803", "intervention-ui", "Intervention UI", "P2", ["R-802", "TE-613"], False, "Teacher panel showing recommendations with one-click assign quiz."),
        ("R-804", "ai-enhanced-recommendations", "AI-Enhanced Recommendations", "P3", ["R-801"], False, "LLM-generated intervention narratives and activity selection."),
        ("R-805", "auto-assign-interventions", "Auto-Assign Interventions", "P3", ["R-802"], False, "Auto-create assignment from intervention recommendation."),
    ]),
    "phase-9-teacher-ai-tutor": ("Phase 9 — Teacher AI Tutor", [
        ("TA-901", "teacher-tutor-prompt", "Teacher Tutor Prompt", "P2", [], False, "Teacher-specific AI prompt for grade-appropriate explanations and practice question generation."),
        ("TA-902", "teacher-tutor-api", "Teacher Tutor API", "P2", ["TA-901"], False, "POST /api/lms/teacher/tutor — separate from student tutor, no student PII."),
        ("TA-903", "teacher-tutor-ui", "Teacher Tutor UI", "P2", ["TA-902"], False, "Dedicated teacher tutor chat widget on teacher dashboard."),
        ("TA-904", "save-to-question-bank", "Save to Question Bank", "P3", ["TA-902", "A-301"], False, "Export AI-generated questions from teacher tutor to question bank."),
    ]),
    "phase-10-integration-polish": ("Phase 10 — Integration & Polish", [
        ("I-1001", "e2e-student-flow", "E2E Student Flow Test", "P0", ["Phases 2-5 MVP"], True, "pytest E2E: login → diagnostic → quiz → progress → learning path."),
        ("I-1002", "e2e-teacher-flow", "E2E Teacher Flow Test", "P0", ["Phase 6 MVP"], True, "pytest E2E: login → PDF upload → MCQ → assign → view results."),
        ("I-1003", "permission-audit", "Permission Audit", "P0", ["F-010"], True, "Security tests: students cannot access other students' data; teachers scoped to own classes."),
        ("I-1010", "e2e-pdf-mcq-pipeline", "E2E PDF→MCQ Pipeline", "P0", ["A-325-A-332"], True, "Integration test: sample Q&A PDF text → extract → MCQ → validate 4 options."),
        ("I-1011", "e2e-manual-diagnostic", "E2E Manual Diagnostic", "P1", ["A-301-A-307"], False, "E2E manual question bank → diagnostic → student flow."),
        ("I-1012", "mcq-quality-gate", "MCQ Quality Gate", "P1", ["A-328"], False, "Additional validation: distractor quality checks, answer key verification."),
        ("I-1004", "error-handling-empty-states", "Error Handling & Empty States", "P1", [], False, "Graceful UI for failed PDF parse, no class, no path, low confidence."),
        ("I-1005", "performance-optimization", "Performance Optimization", "P2", [], False, "Index tuning, N+1 query fixes for LMS dashboards."),
        ("I-1006", "dashboard-modularization", "Dashboard Modularization", "P2", [], False, "Extract LMS sections from monolithic templates into partials/includes."),
        ("I-1007", "monitoring-logging", "Monitoring & Logging", "P2", ["A-329"], False, "Structured logging for PDF pipeline and scoring."),
        ("I-1008", "load-testing-lms", "Load Testing LMS APIs", "P3", [], False, "Extend app/load_testing for LMS endpoints."),
        ("I-1009", "api-documentation", "API Documentation", "P2", ["F-012"], False, "Document all /api/lms endpoints in API_REFERENCE.md."),
    ]),
}

for folder, (phase_name, items) in PHASES_4_10.items():
    for tid, slug, title, pri, deps, mvp, prompt in items:
        task(
            task_id=tid, slug=slug, title=title, phase=phase_name, phase_folder=folder,
            task_type="Backend/Frontend/AI/Testing", priority=pri, dependencies=deps, mvp=mvp,
            description=title + ". See development plan roadmap.",
            objective=prompt.split('.')[0] + ".",
            requirements=[prompt],
            files_create=[f"See task — implement as needed for {tid}"],
            files_modify=["app/routes/lms_routes.py", "templates/"],
            acceptance=[f"{tid} implemented and tested", "Follows existing codebase patterns"],
            constraints=["Do not duplicate existing functionality", "Minimal focused diff", "Reuse existing auth/RAG/LLM"],
            cursor_prompt=f"""You are a senior full-stack engineer implementing task {tid} for IqbalAI LMS.

{prompt}

BEFORE CODING:
1. Read docs/development-plan/README.md and dependency tasks listed for {tid}
2. Inspect existing code in iqbal_ai_stg/app/ — reuse auth, RAG, LLM gateway, RBAC
3. Follow patterns from app/services/lesson/ and app/routes/lesson_routes.py

IMPLEMENTATION RULES:
- Minimal scope — only what this task requires
- Add tests where tests/ already has patterns
- Do not break existing student/teacher dashboards
- Use /api/lms prefix for new APIs
- Register new routes in app/__init__.py

ACCEPTANCE:
- Feature works end-to-end for this task's scope
- RBAC enforced where applicable
- No secrets committed

After implementation, list files changed and how to manually verify.""",
        )


def write_phase_readme(folder: str, phase_name: str, task_ids: list[str]):
    path = ROOT / folder / "README.md"
    lines = [f"# {phase_name}\n", "## Tasks\n"]
    for tid in task_ids:
        matching = [t for t in TASKS if t["task_id"] == tid]
        if matching:
            t = matching[0]
            lines.append(f"- [{t['task_id']} — {t['title']}](./{t['task_id']}-{t['slug']}.md)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    # Write each task file
    by_phase: dict[str, list[str]] = {}
    for t in TASKS:
        folder = t["phase_folder"]
        by_phase.setdefault(folder, []).append(t["task_id"])
        filename = f"{t['task_id']}-{t['slug']}.md"
        filepath = ROOT / folder / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        content = md_task(
            task_id=t["task_id"],
            title=t["title"],
            phase=t["phase"],
            phase_folder=t["phase_folder"],
            task_type=t["task_type"],
            priority=t["priority"],
            dependencies=t["dependencies"],
            description=t["description"],
            objective=t["objective"],
            requirements=t["requirements"],
            files_create=t["files_create"],
            files_modify=t["files_modify"],
            acceptance=t["acceptance"],
            constraints=t["constraints"],
            cursor_prompt=t["cursor_prompt"],
            mvp=t.get("mvp", False),
        )
        filepath.write_text(content, encoding="utf-8")

    # Phase READMEs
    phase_names = {
        "phase-0-existing-system": "Phase 0 — Existing System Analysis",
        "phase-1-foundation": "Phase 1 — Foundation",
        "phase-2-student-core": "Phase 2 — Student Core",
        "phase-3-quiz-assessment-pdf": "Phase 3 — Quiz / Assessment / PDF Pipeline",
        "phase-4-personalization": "Phase 4 — Personalization",
        "phase-5-ai-tutor-practice": "Phase 5 — AI Tutor + Guided Practice",
        "phase-6-teacher-platform": "Phase 6 — Teacher Platform",
        "phase-7-analytics": "Phase 7 — Analytics",
        "phase-8-interventions": "Phase 8 — AI Recommendations",
        "phase-9-teacher-ai-tutor": "Phase 9 — Teacher AI Tutor",
        "phase-10-integration-polish": "Phase 10 — Integration & Polish",
    }
    for folder, ids in by_phase.items():
        write_phase_readme(folder, phase_names.get(folder, folder), ids)

    # Root README
    root_readme = """# IqbalAI LMS Development Plan

Task-by-task implementation prompts for Cursor AI.

## How to Use

1. Complete tasks **in dependency order** (Phase 1 first).
2. Open the task `.md` file for the task you want to implement.
3. Copy the **Cursor Implementation Prompt** section into Cursor chat.
4. Review the diff, run tests, check acceptance criteria.

## Phases

| Phase | Folder | Description |
|-------|--------|-------------|
| 0 | [phase-0-existing-system](./phase-0-existing-system/) | Baseline analysis |
| 1 | [phase-1-foundation](./phase-1-foundation/) | DB schema, services, RBAC |
| 2 | [phase-2-student-core](./phase-2-student-core/) | Student onboarding, dashboard |
| 3 | [phase-3-quiz-assessment-pdf](./phase-3-quiz-assessment-pdf/) | PDF→MCQ pipeline, quizzes |
| 4 | [phase-4-personalization](./phase-4-personalization/) | Learning paths |
| 5 | [phase-5-ai-tutor-practice](./phase-5-ai-tutor-practice/) | AI tutor, guided practice |
| 6 | [phase-6-teacher-platform](./phase-6-teacher-platform/) | Classes, assignments |
| 7 | [phase-7-analytics](./phase-7-analytics/) | Insights, reports |
| 8 | [phase-8-interventions](./phase-8-interventions/) | Recommendations |
| 9 | [phase-9-teacher-ai-tutor](./phase-9-teacher-ai-tutor/) | Teacher AI tutor |
| 10 | [phase-10-integration-polish](./phase-10-integration-polish/) | E2E, security, polish |

## MVP Start Order

1. **F-001** + **A-325** (parallel)
2. F-002 → F-008, F-009, F-010, F-011
3. A-326 → A-329 (PDF pipeline)
4. TE-601 → TE-613 (teacher + assignment)
5. A-303 → A-311 (student quiz + progress)

## Migrations

SQL migrations: [migrations/](./migrations/) (created by F-009)

---
Generated by `_generate_task_prompts.py`
"""
    (ROOT / "README.md").write_text(root_readme, encoding="utf-8")
    (ROOT / "migrations").mkdir(exist_ok=True)
    (ROOT / "migrations" / "README.md").write_text(
        "# LMS Migrations\n\nRun via `scripts/run_lms_migrations.py` (F-009).\n\nOrder: 001_topics → 008_assignments\n",
        encoding="utf-8",
    )
    print(f"Generated {len(TASKS)} task files in {ROOT}")


if __name__ == "__main__":
    main()
