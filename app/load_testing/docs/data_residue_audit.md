# Data Residue Audit Report (Load Testing)

This report investigates what happens to data and files when a load test report is deleted and identifies "residue" items that currently persist in the system.

## 1. Summary of Deletion Behavior

| Data Category | Component | Status on Result Deletion | Status on User Set Deletion |
|---|---|---|---|
| **Test Metadata** | `LoadTestResult` (DB) | **DELETED** | N/A |
| **Test Logs** | `LoadTestLog` (DB) | **DELETED** | N/A |
| **Chat Transcripts** | Injected in Metrics (DB) | **DELETED** | N/A |
| **Extracted Text** | `markdown_exports/*.md` (Disk) | **PERSISTS** | **PERSISTS** |
| **RAG History** | `rag_threads` / `chunks` (DB) | **PERSISTS** | **DELETED** (Cascade) |
| **Generated Lessons**| `lessons` (DB) | **PERSISTS** | **DELETED** (Cascade) |
| **Test Users** | `users` (DB) | **PERSISTS** | **DELETED** |

## 2. Identified Residue (Zombie Data)

### A. Disk-Based Artifacts (`markdown_exports/`)
Whenever a document is ingested during a test (Tests 2, 7, 8), the `rag_service.py` generates a Markdown file containing the full extracted text for user download.
*   **Current Issue**: These files are named `{thread_id}_{filename}.md`. Since the `LoadTestResult` has no direct handle on the filesystem link, these files are currently **never deleted**, leading to disk bloat on the staging server.

### B. RAG Conversation History
Each load test worker creates a unique `thread_id` to isolate its session. 
*   **Current Issue**: These threads and their vector chunks (`rag_chunks`) stay in the database even after the test result is deleted. They are only purged when the entire `TestUserSet` is deleted. This means a long-lived user set can accumulate thousands of "dead" RAG threads over time.

### C. Application-Level Lessons
Tests that simulate "Saving a Lesson" (Test 2, 3) create real entries in the `lessons` table.
*   **Current Issue**: While useful for verification, these lessons remain in the "Finalized" state in the database until the parent user set is deleted.

## 3. Proposed Fix (Phase 10)
To ensure the system remains lean, the following cleanup triggers should be implemented:

1.  **Purge by Thread ID**: When a `LoadTestResult` is deleted, the system should look up all `thread_id`s stored in its artifacts and:
    -   Delete matching files in `markdown_exports/`.
    -   Delete matching entries in `rag_threads`.
2.  **Asset Cleanup**: Improve the `delete_user_set` logic to ensure all associated disk-based uploads (if any beyond the set manager's scope) are identified.
