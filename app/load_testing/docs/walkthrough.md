# Load Testing UI Enhancements – Walkthrough

## Recent Updates: Sequential Test Logic & Form Validation

### 1. Sequential Test Improvements
For Tests 4, 5, and 6 (Sequential Scenarios):
- **Hidden Concurrent Users**: The "Concurrent Users" field is now **hidden** and automatically locked to `1`. This prevents accidental parallel execution for sequential tests.
- **Renamed Field**:
  - For Tests 4 & 5: Field is **"Messages to Send *"**.
  - For Test 6: Field dynamically renames to **"Upload Iterations *"** (since it uploads docs repeatedly, not sends messages).
- **Test 6 Specifics**:
  - **Hidden CSV**: The "Message CSV" field is now **hidden** for Test 6 since it doesn't use it.
- **Respects Limits**: Tests 4 & 5 now respect this limit by slicing the message list.

### 2. Dynamic Form Constraints
We implemented intelligent constraints to prevent invalid test configurations:

| Field | Constraint | Behavior |
|---|---|---|
| **Concurrent Users** | Max ≤ User Set Size | If you select a User Set with 10 users, input max becomes 10. Default auto-sets to 10. |
| **Messages to Send** | Max ≤ CSV Size | If you select a CSV with 15 messages, input max becomes 15. Default auto-sets to 15. |

### Polish & UX Refinements (Batch 2)
Further UI/UX improvements to provide better control and visibility during tests.

### Dashboard Layout & Controls
- **Vertical Restructuring**: The "Start" form and "Live Logs" are now stacked for maximum vertical space.
- **Improved Log Console**: A dedicated dark-mode terminal window with color coding and auto-scroll provides clear feedback.
- **Stop Functionality**: Added a "Stop Test" button that instantly terminates running workers and triggers a summary report.

### 8-Scenario Methodology Alignment
The system has been meticulously aligned with the finalized scenario methodology:
- **Test 1: System Access (Auth & Dashboard)**: Verified Login -> Dashboard -> Logout flow with session cookie validation.
- **Test 2 & 3: Teacher's Heart (Critical Path)**: End-to-end stress test from document upload to lesson save.
- **Test 4: Virtual Classroom (Concurrent Student Chat)**: High-concurrency student interaction test.
- **Test 5: Deep Research (Teacher Long Chat)**: Stress test for deep-context thread stability.
- **Test 6: Intense Study Session (Student Deep Q&A)**: Validation of long-form sequential study.
- **Test 7: Ingest System Reliability (Stability)**: Consistency check for repeated document processing.
- **Test 8: IQ (RAG Quality) Benchmark**: Automated quality scoring using keyword hit analysis.

### Phase 4: Bug Fixes & Celery Alignment

> [!IMPORTANT]
> This phase ensures the load testing system is production-ready for Dockerized environments by moving from memory-based state to persistent database-backed signaling.

#### Key Enhancements
- **Celery Task Wrapper**: Created `load_test_tasks.py` to allow distributed execution of load tests across Celery workers.
- **Global Stop Signal**: The "Stop" button now sets the test status to `STOPPED` in the database. A heartbeat in the `LoadTestRunner` polls this status every 2 seconds, ensuring all workers across different containers terminate gracefully.
- **Reporting Data Passthrough**: Fixed `ReportGenerator` to merge all collected metrics from the JSON database blob directly into the response, ensuring scenario-specific data (Keyword Hits, Ingest Consistency) is always visible.
- **Dashboard UI Stability**:
  - Fixed the bug where the "Concurrent Users" input would reset to default whenever a CSV was selected.
  - Implemented **User Set Filtering**: The dropdown now only shows user sets relevant to the selected role (Teacher vs Student).
  - Added **Rate Limit Alert** card to the report, triggered if any 429 errors are detected during the test.
- **Scenario Instrumentation**: Updated all 8 scenarios to respect the `stop_requested` flag and track 429 status codes.
- **SQLAlchemy Session Fix**: Resolved a critical "Instance not bound to Session" error by isolating the stop signal heartbeat session and expunging user objects after loading.
- **Deadlock Fix in Runner**: Fixed a critical hang where the heartbeat task was blocking the primary test execution from completing.
- **Frontend Status Sync**: Updated `load_testing.html` to recognize the `STOPPED` status.

## Phase 5: Refinement & Stability (Completed)
- **Execution Crash**: Resolved the `filename` undefined error in Test 8 (RAG Quality Benchmark).
- **UI Stability & Logic**:
  - Fixed **Concurrent Users Reset**: Selecting a CSV file no longer resets the user count to default.
  - **Dynamic User Set Management**: Implemented auto-clearing and role-based validation for Teacher vs Student tests.
- **Reporting Enhancements**:
  - **Mandatory Pairing**: Avg Turn Latency is now automatically displayed alongside Messages Sent.
  - **Scenario Instrumentation**: Tests 2, 3, and 4 now correctly populate `latency_trend`.
  - **New Metrics**: Added an "Ingestion Iterations" card for Test 7 (Repeated Ingest).
  - **Generic Rate Limiting**: Refined 429 status code handling to be provider-agnostic.

---

## Technical Architecture Overview

The following diagram illustrates the new unified execution flow:

```mermaid
sequenceDiagram
    participant UI as Load Test Dashboard
    participant API as Load Test API
    participant DB as SQL Database
    participant CW as Celery Worker / Background Thread
    participant Target as App API (Local/Custom)

    UI->>API: POST /api/load-test/start (config)
    API->>DB: Create LoadTestResult (status=running)
    API->>CW: Trigger Task (via Celery or Thread)
    CW->>DB: Poll status every 2s (Heartbeat)
    Loop Test Execution
        CW->>Target: POST /api/rag/chat
        Target-->>CW: 200 OK / 429 Rate Limit
        CW->>DB: Save intermittent logs & metrics
    End
    UI->>API: POST /api/load-test/stop
    API->>DB: Update status to STOPPED
    CW->>DB: Heartbeat sees STOPPED status
    CW->>CW: Terminate workers gracefully
    CW->>DB: Final status: STOPPED + Metrics
    UI->>DB: Polling sees status=STOPPED
    UI->>UI: Show Final Report
```

---

## Verification Results

### Cross-Scenario Reporting
Successfully verified that scenario-specific metrics are persisted and displayed correctly:
- **Test 1**: Logout Verification badge shown.
- **Test 3**: All responses received checkmark.
- **Test 7**: Ingest Consistency (SD) calculated and displayed.
- **Test 8**: Keyword analysis aggregation.

### Stop Functionality
Verified that clicking "Stop Test" immediately sends a signal to the database, which is picked up by the background runner, leading to a "Stop signal detected in Database" log and graceful worker termination.

### Advanced Reporting & Scenario-Aware Metrics
To fulfill the "Primary Checks" and "Goals" from the scenario methodology, we implemented a dynamic reporting layer:
- **Scenario-Specific Cards**: The dashboard now automatically displays context-relevant metrics:
  - **T8 Benchmark**: Displays "Total Keyword Hits".
  - **T7 Stability**: Displays "Ingest Consistency (Standard Deviation)".
  - **Stress Tests (T5/T6)**: Displays "Avg Turn Latency" to show performance trends.
- **Primary Check Badges**: A dedicated verification section confirms critical path completion:
  - **Logout Verified**: Confirms Test 1 reached the end state.
  - **Lesson Saved to DB**: Confirms Test 2/3 successfully persisted data.
  - **Chat Sequence Complete**: Confirms sequential stressors hit their target iteration depth.
- **Data Persistence**: Metrics are now stored in a structured JSON format in the database, allowing for historical trend analysis.

Detailed alignment status can be found in [load_testing_scenario_audit.md](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/docs/load_testing_scenario_audit.md).
Executive non-technical report: [load_testing_executive_summary.md](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/docs/load_testing_executive_summary.md).
API Inventory: [load_testing_api_inventory.md](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/docs/load_testing_api_inventory.md).
Reporting Gap Analysis (Implemented): [reporting_gap_analysis.md](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/docs/reporting_gap_analysis.md).

### Data Integration
- **Lesson Selection**: Replaced manual "Lesson ID" entry with a dynamic dropdown fetching finalized lessons from the database.
- **Auto-Reporting**: The results modal now opens automatically upon completion or manual stop.

### Verification of Batch 2 Improvements
The following recording demonstrates the new layout, lesson selection, stop functionality, and auto-modal triggers.

![Verification Dashboard Improvements](/Users/abdurrehman/.gemini/antigravity/brain/c3595427-45c1-4b79-ad06-236310d9bebb/verify_ui_restructure_1771560582471.webp)

### 3. Visual Verification

Tested in browser (screenshots from `form_constraints_verify` session):

````carousel
![Initial State - Concurrent Users visible](/Users/abdurrehman/.gemini/antigravity/brain/c3595427-45c1-4b79-ad06-236310d9bebb/initial_state_1771555372797.png)
<!-- slide -->
![User Set Selected - Max constraint applied](/Users/abdurrehman/.gemini/antigravity/brain/c3595427-45c1-4b79-ad06-236310d9bebb/user_set_selected_1771555416279.png)
<!-- slide -->
![Sequential Test - Concurrent hidden, Messages visible](/Users/abdurrehman/.gemini/antigravity/brain/c3595427-45c1-4b79-ad06-236310d9bebb/test_5_message_csv_selected_1771555486320.png)
````

### 4. Code Changes
- **Frontend**:
    - Added `updateFieldConstraints()` logic in `load_testing.html` to sync option `data-count` attributes with input `max` attributes.
    - Updated `updateFormFields()` to dynamically rename labels and hide unused fields (CSV for Test 6, Concurrent Users for sequential).
- **Backend**: Updated `test_student_sequential.py` and `test_teacher_sequential.py` to slice message lists based on `config.requests_per_user`.
