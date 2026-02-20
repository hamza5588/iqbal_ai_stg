# Reporting Enhancement Implementation Plan

This plan outlines the technical steps to surface deep metrics and "Primary Check" verification in the Load Testing Dashboard.

## Proposed Changes

### 1. Data Layer & Core Engine
To store specialized metrics, we need to expand the shared summary object and the persistence logic.

#### [MODIFY] [config.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/config.py)
- Expand `TestResultSummary` to include:
  - `successful_logouts: int` (For Test 1)
  - `keyword_hits: int` (For Test 8)
  - `consistency_stdev: float` (For Test 7)
  - `latency_trend: List[float]` (For Stress Tests 5 & 6)
  - `lesson_saved: bool` (For Test 2/3)

#### [MODIFY] [runner.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/runner.py)
- Update `_save_metrics()` to map these new summary fields into the JSON `metrics` column in the database.

---

### 2. Scenario Instrumentation
Each scenario must be updated to populate the new fields in the `summary` object.

#### [MODIFY] [test_auth.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/scenarios/test_auth.py)
- Increment `summary.successful_logouts` upon successful logout.

#### [MODIFY] [test_teacher_flow.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/scenarios/test_teacher_flow.py)
- Set `summary.lesson_saved = True` upon successful DB persistence.

#### [MODIFY] [test_rag_pipeline_quality.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/scenarios/test_rag_pipeline_quality.py)
- Populate `summary.keyword_hits` using the existing analysis logic.

#### [MODIFY] [test_teacher_repeat_ingest.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/scenarios/test_teacher_repeat_ingest.py)
- Calculate and populate `summary.consistency_stdev`.

#### [MODIFY] [Sequential Tests (T5/T6)](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/scenarios/)
- Record `duration` of each chat turn into `summary.latency_trend`.

---

### 3. Frontend Dashboard
Update the reporting interface to be "Scenario Aware."

#### [MODIFY] [load_testing.html](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/templates/admin/load_testing.html)
- **Dynamic Cards**: Modify `generateReport()` to show different cards based on `test_type`:
  - Show **Keyword Hits** for Test 8.
  - Show **Ingestion Consistency** for Test 7.
- **Primary Check Badges**: Add a "Verification" section displaying green/red checkmarks for:
  - `Login Success`
  - `Logout Verified` (If applicable)
  - `Lesson Saved` (If applicable)
- **Latency Trend**: Render a simple list or "Stability Score" for Stress Tests.

---

## Verification Plan

### Automated Verification
- Run **Test 1** and verify the "Logout Verified" badge appears in the report.
- Run **Test 8** and verify "Total Keyword Hits" appears as a metric card.
- Run **Test 7** and verify "Ingestion Stability (SD)" is visible.

### Safety Checks
- **Backward Compatibility**: Ensure that old test results without these new fields still render correctly (using optional chaining/null checks in JS).
- **Concurrency Safety**: Verify that updating the shared `summary` object across concurrent workers does not cause race conditions (using atomic increments or thread-safe patterns).
