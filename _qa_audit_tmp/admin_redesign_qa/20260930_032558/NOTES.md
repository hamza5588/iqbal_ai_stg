# Admin redesign QA — 2026-09-30 03:25 (local, http://127.0.0.1:5057)

Run: `venv/Scripts/python.exe _qa_audit_tmp/admin_redesign_qa/run_admin_qa.py`.
Viewports: desktop 1440×900 and mobile 390×844. Admin account: `e2e.stu.admredesign@iqbalai.local`.

## Result: 67 of 67 checks pass
- **Every destination** opens with the correct sidebar active state and `#hash`:
  - Dashboard, Users, Lessons, Documents, Diagnostic, LLM Settings, RAG Prompts, Coupons, Color Theme
  - Load Testing (all 4 tabs) and LLM Telemetry
- **Data loads:**
  - users table, role pills, Teachers sub-tab filter (only teacher rows), empty state
  - lessons, documents and coupons tables
  - diagnostic library, the 4 workflow steps, the "Add study PDFs" tab, library filters
  - LLM provider, RAG prompt text, 10 theme presets
- **Safe writes** (throwaway records):
  - created coupon `QAADM…` → deleted
  - created user `qa_admin_ui_…` → edit modal prefilled → deleted
- **Not saved on purpose:** LLM settings, RAG prompts and the platform theme. These were read-only or preview-only checks.
- **Cross-page links:** Load Testing → sidebar Diagnostic lands on `/admin/#diagnostic`.
- Static assets returning 4xx: **0**. Page JS errors: **0**. Horizontal overflow: **0 px** on every page, both viewports.
- **API errors: 2, both known and pre-existing.** `GET /admin/llm-telemetry/api/summary` returns 500 with `fromisoformat: argument must be str`. That is a backend bug outside this redesign. The page shows the error inline, as before.

## Family check (screenshots/ref_teacher.png, ref_student.png vs desktop_*.png)
**Same as teacher/student:**
- white 68px bar with the logo alone on the left and the avatar on the right
- `#f4f7fc` canvas
- white 14px cards on a `#e2e8f0` line
- page heads: 52px blue-50 icon badge, Poppins 24px blue-900 title, one muted line
- `#f1f6fd` table headers in sentence case
- 12px/700 pills in the same green/amber/red/grey meanings
- 10px-radius blue-700 primary and line-border outline buttons
- inputs filled `#f4f7fc` with a blue focus ring
- sub-tabs with counts and a 3px underline
- diagnostic library rows with a 6px left status bar
- illustrated icons (Lessons, Diagnostic, AI Tutor, AI chat bot, Analytics)

**Adapted for the admin:**
- a grouped sidebar instead of the icon top nav (11 destinations)
- tables instead of list rows for scanning people and records
- a status-overview home instead of a learning journey
- numbered publish steps for the diagnostic upload
- segmented tabs for Load Testing's sub-views
- a mobile drawer instead of the wrapped top nav

## Screenshots
`screenshots/` holds desktop and mobile shots of every section, both modals, the avatar menu, the toast, the users empty state, the diagnostic append tab, all Load Testing tabs, Telemetry, and the teacher/student references.
