# Load Testing Technical Audit Report

## 1. System Architecture
The Load Testing module is a self-contained stress-testing suite integrated into the Admin dashboard. It uses an **Asynchronous Runner** pattern to simulate real-world traffic.

### Flow Logic:
1.  **Frontend (UI)**: User configures the test in `templates/admin/load_testing.html`. 
    *   **Dynamic Constraints**: JavaScript automatically calculates `max` values for Concurrent Users (based on User Set size) and Messages (based on CSV size).
    *   **Proxy Strategy**: All requests use a global `fetchWithProxy` to ensure compatibility regardless of deployment environment.
2.  **API (Start)**: `/api/load-test/start` receives the configuration.
    *   It validates the request and creates a `LoadTestResult` record in the database with status `PENDING`.
    *   It initializes the `LoadTestRunner` and spawns it in a dedicated **Background Thread** using `threading.Thread`.
3.  **Runner (Execution)**: `app/load_testing/runner.py` manages the lifecycle.
    *   **Live Passwords**: It joins the `TestUser` table with the main `User` table to ensure it always uses current passwords (Live Sync).
    *   **Async Core**: Uses `asyncio` and `aiohttp` to run multiple "User Workers" concurrently.
    *   **Dedicated Sessions**: Each virtual user gets its own `ClientSession` and `CookieJar`, perfectly simulating independent browsers.
4.  **Reporting**: Once finished, the runner updates the status to `COMPLETED` and saves aggregated metrics (Success Rate, Messages Sent, KB processed).

---

## 2. Test Scenario Logic

| Test ID | Name | Core Logic |
|---|---|---|
| **1** | **Multi-User Sign-In** | Tests the auth bottleneck. Runs multiple logins concurrently and verifies session cookie generation. |
| **2** | **Teacher Flow (Concurrent)** | E2E Stress. Each user: Login -> Upload PDF -> Poll Status -> Chat 1-N times -> Finalize Lesson -> Save to DB. |
| **3** | **Student Chat (Concurrent)** | Interaction Stress. Simulated students join a specific Lesson ID and send messages from a CSV. |
| **4** | **Teacher Sequential** | RAG Pipeline stress. A single user sends 10-50 messages sequentially, waiting for each response. Measures response time stability. |
| **5** | **Student Sequential** | Same as Test 4, but for the Student-Lesson chat endpoint. |
| **6** | **Document Upload Repeat** | Indexing stress. Repeatedly uploads the SAME document set to verify Milvus/Chroma consistency and ingestion speed. |
| **7** | **RAG Quality Benchmark** | Quality audit. For every doc in a set, it uploads and asks the "Golden Questions" from CSV, logging accuracy/stability. |

---

## 3. Implementation Status Audit

### ✅ Verified Features:
*   **Sequential Test Lock**: UI correctly hides 'Concurrent Users' and sets value to 1 for tests 4, 5, 6.
*   **Iterative Chat**: Tests 2, 3, 4, 5 successfully load messages from uploaded CSVs and iterate through them.
*   **Granular Logging**: Scenarios now log per-step durations (e.g., `Upload success in 450ms`) and final durations.
*   **Dynamic Labels**: Test 6 correctly renames "Messages" to "Upload Iterations".
*   **Cleanup**: Implemented "Delete All" for results to keep the database lean.

### ⏸ Deferred Features:
*   **RAG Quality Metrics**: Postponed. The logic for calculating "Retrieval Confidence" (Cosine Similarity bubble-up) is documented but not implemented in core services to avoid side-effects.

---

## 4. Maintenance Notes
*   **Core Services**: No changes were made to `rag_service.py` or `rag_routes.py` in the final build, ensuring regular user traffic is untouched.
*   **Database**: `LoadTestResult` stores all configurations for easy re-running/auditing.
