# Load Testing: API Inventory Report

This report documents every API endpoint hit during the current load testing suite. All tests strike the **core logic** of the Iqbal AI application, ensuring performance metrics accurately reflect the user experience.

---

## Global Common Step
Every test starts with authentication using real user data:
*   **POST** `/auth/login`: Validates user credentials and establishes a secure session.

---

## Scenario-Wise API Mapping

### Test 1: System Access (Auth & Dashboard)
*   **GET** `/`: Renders the main dashboard.
*   **GET** `/logout`: Safely terminates the user session.

### Test 2 & 3: The "Teacher's Heart" - Critical Path
*   **POST** `/create_conversation`: Initializes a new workspace.
*   **POST** `/api/rag/ingest`: Uploads and starts AI analysis of the document.
*   **GET** `/api/rag/ingest/status/<task_id>`: Tracks real-time processing progress.
*   **POST** `/api/rag/chat`: Concurrent/multi-step AI chat interaction.
*   **GET** `/api/rag/thread/<thread_id>/finalized-lesson`: Retrieves the AI-drafted lesson.
*   **POST** `/api/lessons/create`: Persists the finalized lesson to the database.

### Test 4: The "Virtual Classroom" (Concurrent Student Chat)
*   **POST** `/api/lessons/ask_question`: Direct Q&A against finalized lesson data.

### Test 5: Deep Research (Teacher Long Chat)
*   **POST** `/api/rag/chat`: Repeated hits to the same thread to test memory and depth.

### Test 6: Intense Study Session (Student Deep Q&A)
*   **POST** `/api/lessons/ask_question`: Continuous sequential questions in a single lesson context.

### Test 7: Ingest System Reliability (Repeated Processing)
*   **POST** `/api/rag/ingest`: Repeatedly hits the ingestion pipeline to check for performance drift.

### Test 8: IQ (RAG Quality) Benchmark
*   **POST** `/api/rag/chat`: Hits the AI chat with standard questions to evaluate response quality.

---

## Summary of Integration
All load tests hit endpoints defined in core route files: `auth.py`, `chat.py`, `rag_routes.py`, and `lesson_routes.py`.
