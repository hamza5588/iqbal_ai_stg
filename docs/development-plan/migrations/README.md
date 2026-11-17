# LMS Migrations

Run via `scripts/run_lms_migrations.py` (F-009).

Order: 001_topics → 008_assignments

## Adaptive question metadata (012)

`012_question_metadata.sql` adds `questions.domain`, `topic_label`, and
`cognitive_level`; difficulty already exists. The equivalent idempotent startup
migration in `question_metadata_migration.py` supports SQLite and PostgreSQL.
Existing rows remain readable with null metadata. Reimport old diagnostic and
question-bank PDFs to populate all four fields; new imports populate them before
saving. Use a disposable database to validate migrations before deployment.
