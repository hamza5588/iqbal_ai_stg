# Load Testing System Limitations Report

This report documents all built-in limitations, constraints, and boundaries currently enforced within the Iqbal AI Load Testing system, covering the UI, Asset Management, and Execution Engine.

## 1. Frontend & Configuration Limits
These constraints are enforced directly in the Admin Dashboard UI to prevent system overload.

| Field | Constraint | Logic / Rationale |
|---|---|---|
| **User Set Count** | **Max 1000 Users** | Increased from 50 to 1000 to support larger scale testing. |
| **Concurrent Users** | **1 to 1000** | Clamped only by the size of the selected User Set. Max boundary increased to 1000. |
| **Messages per User** | **1 to [CSV Rows]** | Clamped to the number of rows in the selected Message CSV asset. |
| **Sequential Tests** | **Hard-locked to 1 User** | Tests 4, 5, 6, and 8 ignore the "Concurrent Users" input and force a single-user flow to protect the RAG pipeline. |
| **Test Duration** | **Currently Ignored** | The `duration_seconds` field is present in the UI/Config but is not yet implemented in the Runner. Tests run until the target message count/iteration count is finished. |

## 2. Asset Management Constraints
Limitations regarding the files and users used for testing.

*   **Document Sets**:
    *   **Format**: Only `.pdf` files are accepted.
    *   **Size**: Limited by standard Nginx/Flask upload limits (typically 16MB-32MB).
*   **Message CSVs**:
    *   **Format**: Only `.csv` files are accepted.
    *   **Structure**: Only the **first column** of the CSV is read as the message text.
    *   **Header Logic**: Automatically detects and skips common headers like `message`, `prompt`, `text`, or `question`.
*   **Test Users**:
    *   **Role Enforcement**: Tests are role-aware. You cannot use a "Student" user set for a "Teacher" test scenario (and vice versa).

## 3. Backend Execution & Safety
Internal boundaries designed to prevent CPU spinning or "zombie" processes.

*   **Polling Frequency**:
    *   Mandatory **2.0s delay** between every status check request (Ingestion/RAG). This prevents the "Busy Loop" bug and keeps CPU usage at <5%.
*   **Wait Thresholds (Timeouts)**:
    *   **Concurrent Scenarios (T2, T6)**: Max **60 seconds** of polling for ingestion before marking as "Timed Out".
    *   **RAG/Quality Scenarios (T7, T8)**: Max **120 seconds** of polling per document.
    *   **Global Network Timeout**: **300 seconds (5 minutes)** hard-cap per HTTP request. Catch-all for hung connections.
*   **Memory Management**:
    *   **CSV Loading**: Large CSVs are loaded into memory at the start of the test. Extremely large files (e.g. 10,000+ rows) may cause memory pressure on the staging server.

## 4. Scaling Boundaries
*   **Database Logs**: Live logging is capped at the **last 100 entries** per request to prevent browser lag during long tests.
*   **Parallel Tests**: The system supports multiple *different* tests running in the background, but they share the same backend worker pool (Threading), which can lead to contention if total concurrent users across all tests exceed ~200.
