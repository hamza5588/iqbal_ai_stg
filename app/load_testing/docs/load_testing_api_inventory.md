# Load Testing: API Inventory Report

This report documents every API endpoint hit during the current load testing suite and the reporting phase. All tests strike the **core logic** of the Iqbal AI application.

---

## 1. Global Authentication
Every test establishing a session starts here:
*   **POST** `/auth/login`: Credential validation and session establishment.
*   **GET** `/logout`: Graceful session termination.

---

## 2. Load Testing Infrastructure APIs
These endpoints manage the lifecycle of a load test:
*   **POST** `/api/load-test/start`: Spawns the background Runner.
*   **POST** `/api/load-test/stop`: Signals active workers to terminate.
*   **GET** `/api/load-test/status`: Real-time polling for test progress.
*   **DELETE** `/api/load-test/result/<id>`: Selective data purge of results.

---

## 3. Reporting & Analysis (NEW)
Endpoints used to retrieve and analyze test performance:
*   **GET** `/api/load-test/report/<id>/technical`: Aggregated system metrics (JSON).
*   **POST** `/api/load-test/report/<id>/executive`: LLM-powered performance analysis.
*   **GET** `/api/load-test/report/<id>/artifacts`: Discovery of transcripts/lessons.
*   **GET** `/api/load-test/artifact/download`: retrieval of generated Markdown files.

---

## 4. Scenario-Wise Core Hits

### Test 1: System Access
- **GET** `/`
- **GET** `/student-dashboard` or `/teacher-dashboard`

### Tests 2, 4, 6, 7 (RAG & Ingestion)
- **POST** `/api/rag/ingest`: File upload and processing.
- **GET** `/api/rag/ingest/status/<task_id>`: Polling for completion.
- **POST** `/api/rag/chat`: AI interaction for teachers.
- **GET** `/api/rag/thread/<id>/finalized-lesson`: Lesson retrieval.

### Tests 3, 5, 8 (Student Chat & Quality)
- **POST** `/api/lessons/ask_question`: Interaction with finalized lessons.
- **POST** `/api/lessons/create`: Persisting lesson drafts.

---

## Summary
The suite provides 100% coverage of the critical path endpoints in `auth.py`, `chat.py`, `rag_routes.py`, and `lesson_routes.py`.
