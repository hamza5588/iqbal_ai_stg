# Load Testing Technical Audit Report

## 1. System Architecture
The Load Testing module is a self-contained stress-testing suite integrated into the Admin dashboard. It uses an **Asynchronous Runner** pattern to simulate real-world traffic.

### Flow Logic:
1.  **Frontend (UI)**: User configures the test in `templates/admin/load_testing.html`. 
2.  **API (Start)**: `/api/load-test/start` initializes the test. It checks the `USE_CELERY_FOR_INGESTION` flag; if enabled, it dispatches the test as a **Celery Task**. Otherwise, it spawns a **Background Thread** managed by the Flask app.
3.  **Runner (Execution)**: `app/load_testing/runner.py` manages the active test using `asyncio` and `aiohttp`. This core logic is shared between the Celery worker and the direct thread runner.
4.  **Reporting & Analysis**: Status is updated to `COMPLETED` and results are saved to `LoadTestResult`.

## 2. Phase 14: AI Analysis & Visualizations (NEW)
We have integrated a premium AI analysis layer to transform raw metrics into actionable insights.

### Backend: LLM Analysis Logic
- **Module**: `app/load_testing/report.py`
- **Engine**: Uses `app.models.ChatModel` to interface with Groq/OpenAI.
- **Prompt Engineering**: The system uses a "Senior Performance Engineer" persona. It receives the **full JSON Technical Report**, allows the LLM to analyze the 100 most recent detailed logs, and evaluates success against a 95% threshold.
- **Persistence**: Results are saved to the `llm_analysis` field in the database for instant retrieval.

### Frontend: Rich Rendering & Charts
- **Markdown Rendering**: Uses `marked.js` CDNs to render the LLM's Markdown output into rich HTML.
- **Visualization Engine**: `Chart.js` is used to generate dynamic visuals in the browser:
    - **Latency Trajectory**: A custom JavaScript parser extracts "Iteration Time" patterns from raw logs to plot performance over time.
    - **Status Distribution**: A doughnut chart visualizes the success/failure ratio.
- **UX**: Implemented a stateful loading spinner and defensive `null-checks` to prevent UI crashes on missing data.

## 3. Test Scenario Logic

| Test ID | Name | Core Logic |
|---|---|---|
| **1** | **System Access** | Tests auth bottleneck. Runs multiple logins concurrently. |
| **2** | **Teacher Flow** | E2E Stress. Login -> Upload PDF -> Poll -> Chat -> Finalize Lesson -> Save. |
| **3** | **Student Chat** | Interaction Stress. Simulated students join a specific Lesson ID. |
| **4** | **Teacher RAG Seq.** | RAG Pipeline depth. Sequential messages in a single thread. |
| **5** | **Student Lesson Seq.** | Same as Test 4, for the Student-Lesson endpoint. |
| **6** | **Repeated Ingest** | Indexing stress. Repeatedly uploads the SAME document. |
| **7** | **Ingest Stress** | Heavy parallel ingestion without chat. |
| **8** | **RAG Quality** | Accuracy benchmark. Runs "Golden Questions" and logs AI stability. |

## 4. Maintenance & Safety
- **Scaling Limit**: Verified up to **1,000 concurrent users**.
- **CPU Protection**: Mandatory 2s polling loops in the runner to prevent thread starvation.
- **Cleanup**: Selective purging of thread IDs and markdown artifacts on test deletion.
- **Security**: Ensures `GROQ_API_KEY` is never exposed to the frontend; it is retrieved securely from `session` or `config` on the backend.
