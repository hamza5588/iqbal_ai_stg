# Load Testing System for Iqbal AI

## Planning
- [x] Analyze codebase: auth flow, API endpoints, RBAC, DB models
- [x] Write implementation plan (v1)
- [x] Incorporate user feedback (v2, v3) — localhost, deletable assets, LLM on-demand, Test 7
- [ ] Get user approval on implementation plan

## Execution — Core Module
- [/] Create `app/load_testing/` package structure
- [/] Implement `config.py` — test configuration dataclasses
- [/] Implement `runner.py` — async test execution engine
- [/] Implement `report.py` — dual-format report generator + LLM analysis
- [/] Implement `user_set_manager.py` — test user set CRUD

## Execution — Test Scenarios
- [/] Test 1: Multi-user sign-in (`test_auth.py`)
- [/] Test 2: Teacher full flow (`test_teacher_flow.py`)
- [/] Test 3: Multi-student lesson chat (`test_student_chat.py`)
- [/] Test 4: Single teacher sequential RAG chat (`test_teacher_sequential.py`)
- [/] Test 5: Single student sequential lesson chat (`test_student_sequential.py`)
- [/] Test 6: Same doc N times + chunk/vector comparison (`test_teacher_repeat_ingest.py`)
- [/] Test 7: Multi-file RAG pipeline quality benchmark (`test_rag_pipeline_quality.py`)

## Execution — Database & Routes
- [/] Add new DB models (TestUserSet, TestDocument, LoadTestConfig, LoadTestReport, etc.)
- [/] Implement `load_test_routes.py` — admin API routes
- [x] Register blueprint in `app/__init__.py`
- [x] Implement `DocumentSetManager` for handling file uploads
- [x] Add API routes for Document Sets (`create`, `upload`, `list`, `delete`)

## Execution — Admin UI
- [x] Update `load_testing.html` to include Document Set management UI
## Execution — Admin UI
- [/] Update `load_testing.html` to include Document Set management UI
- [ ] Implement "Global Settings" tab (Base URL, Admin API Key) for remote asset management
- [x] Build `load_testing.html` — dashboard with 3 tabs (Assets, Tests, Reports)
- [x] Implement "Live Logs" console in `load_testing.html`
- [x] Implement "Detailed HTML Report" view in `load_testing.html`

## Verification
- [x] Local sanity check (imports & dependencies)
- [ ] Dry-run test against staging (Ready for manual test)
- [ ] Validate reports and LLM analysis
- [ ] Create walkthrough
