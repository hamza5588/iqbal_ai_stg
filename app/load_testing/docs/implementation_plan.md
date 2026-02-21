# Bug Fixes & Infrastructure Alignment Plan

This plan addresses reported UI bugs, reporting inaccuracies, and infrastructure considerations for Docker/Celery environments.

## Proposed Changes

### 1. Unified Background Execution (Celery & DB-Backed)

#### [NEW] [load_test_tasks.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/tasks/load_test_tasks.py)
- **Goal**: Provide a distributed-safe entry point for load tests.
- **Implementation**:
  - Define `run_load_test_task` that initializes `LoadTestRunner` and calls `.run()`.
  - Runs inside Flask app context (via `create_app()`).

#### [MODIFY] [load_test_routes.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/routes/load_test_routes.py)
- **Start Test**: Change logic to trigger Celery task via `.delay()` if `USE_CELERY_FOR_INGESTION` is True; fall back to background thread otherwise.
- **Stop Test**: Simplify logic to just set `status = LoadTestStatus.STOPPED.value` in the database. This acts as a global kill-switch for Docker/distributed workers.

#### [MODIFY] [runner.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/runner.py)
- **Heartbeat & Stop Polling**:
  - In the main worker loop, periodically (every 2-5s) fetch the latest `LoadTestResult` from the DB.
  - If `status == STOPPED`, terminate all async workers gracefully.
- **Status Mapping**: Map `LoadTestStatus.STOPPED` correctly in logs and database.

---

### 2. Reporting & Metrics Fixes

#### [MODIFY] [scenarios/*.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/scenarios/)
- **Goal**: Respect "Stop" signal and track rate limiting.
- Change: In all loops (chats, ingest polls), check `summary.stop_requested`.
- Change: Check for `429` status codes and increment `summary.rate_limit_hits`.
- **Test 3 Specific**: Add a check to confirm all messages were received for the "All responses received" metric.
- **Test 7 Specific**: Optimize logging order by ensuring iteration logs are flushed before status updates.

---

### 3. Dashboard Fixes (templates/admin/load_testing.html)
- **Fix Concurrent Users Reset**: Update `updateFieldConstraints` to only reset the input if it's invalid, rather than on every CSV change.
- **User Set Filtering**: Modify `loadUserSets` to filter role based on test type (Teacher vs Student).
- **Test 8 Fix**: Show CSV select field for `rag_quality_benchmark`.
- **Report Badges**: Fix logic in `generateReport` to correctly check for `successful_requests` and `lesson_saved` now that they are available in the response.
- **New Metrics**:
  - Add **Avg Message Response Time** card (calculated from `latency_trend`).
  - Add **Rate Limit Alert** card (visible only if `rate_limit_hits > 0`).
  - Add **All Responses Received** checkmark for Test 3.

---

### 4. Infrastructure (Celery & Docker)
- **Core Logic Interaction**: Since the LoadTestRunner calls the app's existing APIs, it will naturally trigger the existing Celery-based ingestion if it's enabled. The runner already polls the ingest status, so no changes are needed to "wait" for the core Celery tasks.

---

## Phase 5: Refinement & Stability

> [!NOTE]
> This phase addresses UI bugs, execution crashes, and reporting gaps identified during final verification.

### [Component] Frontend Dashboard
#### [MODIFY] [load_testing.html](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/templates/admin/load_testing.html)
- **Fix Input Reset**: Prevent `concurrent-users` from resetting to default when selecting a CSV file.
- **Auto-Clear User Sets**: Clear the "User Set" selection if the selected set's role (Teacher/Student) doesn't match the required role for the new test type.
- **Start-Test Validation**: Add a check to ensure a User Set is selected and matches the test's role requirements.
- **Enhanced Metrics Display**: 
  - Show "Avg Message Response Time" for Tests 2, 3, and 4.
  - **Mandatory Pairing**: Ensure that whenever a "Msgs Sent" card is displayed, an "Avg Turn Latency" card is also shown with relevant data.
  - Show "Ingestion Iterations" for Test 7.

### [Component] Load Test Scenarios
#### [MODIFY] [test_rag_pipeline_quality.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/scenarios/test_rag_pipeline_quality.py)
- **Fix Crash**: Define `filename` correctly from `doc.filename`.
- **Metrics**: Populate `latency_trend`.

#### [MODIFY] [test_teacher_flow.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/scenarios/test_teacher_flow.py)
- **Metrics**: Populate `latency_trend` during iterative chat.

#### [MODIFY] [test_student_chat.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/scenarios/test_student_chat.py)
- **Metrics**: Populate `latency_trend` during chat sequence.

#### [MODIFY] [test_teacher_repeat_ingest.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/scenarios/test_teacher_repeat_ingest.py)
- **Metrics**: Track `ingestion_iterations`.

### [Component] Configuration & Runner
#### [MODIFY] [config.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/config.py)
- Add `ingestion_iterations: int = 0` to `TestResultSummary`.

#### [MODIFY] [runner.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/runner.py)
- Ensure `ingestion_iterations` is saved in `_save_metrics`.

## Verification Plan
- Run Test 8 and verify it completes without `filename` error.
- Verify User Set dropdown clears correctly when alternating between Teacher/Student tests.
- Verify "Avg Message Response Time" appears for Test 2, 3, 4.
- Verify "Ingestion Iterations" appears for Test 7.
