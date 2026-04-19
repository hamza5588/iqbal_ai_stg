
# Postman Load Testing Implementation Plan

This document outlines the technical specifications for implementing the Iqbal AI Load Testing suite using Postman's automated testing features (Collection Runner / Postman CLI).

## 1. Prerequisites & Setup

### Environment Variables
Configure a Postman Environment with the following variables:
- `base_url`: Target URL (e.g., `http://localhost:5000` or `https://staging.iqbalai.com`)
- `teacher_email`: Email for teacher capabilities
- `teacher_password`: Password for teacher
- `student_email`: Email for student capabilities
- `student_password`: Password for student
- `doc_path`: Local path to a PDF file (for Newman/CLI runs) or manually selected in Runner

### Collection Structure
Organize the collection into folders corresponding to each test type. Use **Pre-request Scripts** for setup (like calculating timestamps) and **Tests** scripts for assertions and capturing variables (like `token`, `conversation_id`, `thread_id`) to pass between requests.

---

## 2. Common Workflows

### Authentication
Start every test flow with a Login request to establish a session.
- **Endpoint**: `POST {{base_url}}/auth/login`
- **Body**: `form-data`
    - `useremail`: `{{teacher_email}}` (or `{{student_email}}`)
    - `password`: `{{teacher_password}}`
- **Tests Script**:
    ```javascript
    pm.test("Login Successful", function () {
        pm.response.to.have.status(200);
    });
    // Capture cookies automatically handled by Postman
    ```

---

## 3. Test Scenarios

### Test 1: Multi-User Sign-In & Dashboard Access
**Objective**: Verify that multiple concurrent users can login and load the dashboard without latency or errors.

1.  **Login** (see Common Workflows)
2.  **Access Dashboard**
    -   **Endpoint**: `GET {{base_url}}/`
    -   **Tests**:
        ```javascript
        pm.test("Dashboard Loaded", function () {
            pm.response.to.have.status(200);
            pm.expect(pm.response.text()).to.include("Welcome"); // or known dashboard text
        });
        ```

### Test 2: Teacher Full Flow (Upload -> Chat -> Lesson)
**Objective**: End-to-end test of the teacher's primary workflow.

1.  **Login** (Teacher)
2.  **Create Conversation**
    -   **Endpoint**: `POST {{base_url}}/create_conversation`
    -   **Body** (JSON): `{"title": "Postman Load Test"}`
    -   **Tests**:
        ```javascript
        var data = pm.response.json();
        pm.environment.set("conversation_id", data.conversation_id);
        ```
3.  **Upload PDF (Ingest)**
    -   **Endpoint**: `POST {{base_url}}/api/rag/ingest`
    -   **Body**: `form-data`
        -   `file`: (Select PDF file)
        -   `create_new_thread`: `true`
        -   `conversation_id`: `{{conversation_id}}`
    -   **Tests**:
        ```javascript
        var data = pm.response.json();
        pm.environment.set("task_id", data.task_id);
        ```
4.  **Poll Ingest Status**
    -   **Endpoint**: `GET {{base_url}}/api/rag/ingest/status/{{task_id}}`
    -   **Logic**: Use Postman's `postman.setNextRequest()` to loop this request until `status === 'success'`.
    -   **Tests**:
        ```javascript
        var data = pm.response.json();
        if (data.status === "success") {
            pm.environment.set("thread_id", data.thread_id);
        } else if (data.status === "processing") {
            postman.setNextRequest("Poll Ingest Status"); // Loop
            setTimeout(() => {}, 2000); // Wait 2s
        } else {
            pm.expect.fail("Ingest failed");
        }
        ```
5.  **RAG Chat**
    -   **Endpoint**: `POST {{base_url}}/api/rag/chat`
    -   **Body** (JSON):
        ```json
        {
            "message": "Create a lesson plan based on this document.",
            "thread_id": "{{thread_id}}",
            "conversation_id": "{{conversation_id}}"
        }
        ```
6.  **Finalize Lesson Check**
    -   **Endpoint**: `GET {{base_url}}/api/rag/thread/{{thread_id}}/finalized-lesson`
7.  **Save Lesson**
    -   **Endpoint**: `POST {{base_url}}/api/lessons/create`
    -   **Body** (JSON):
        ```json
        {
            "title": "Postman Generated Lesson",
            "content": "Lesson content derived from RAG...",
            "focus_area": "General",
            "grade_level": "General"
        }
        ```

### Test 3: Multi-Student Lesson Chat
**Objective**: Simulate multiple students chatting about a specific lesson.

1.  **Prerequisite**: A valid `lesson_id` (set manually in env or created via Teacher flow).
2.  **Login** (Student)
3.  **Start Lesson Chat**
    -   **Endpoint**: `POST {{base_url}}/api/chat/start_lesson_chat`
    -   **Body** (JSON): `{"lesson_id": "{{lesson_id}}"}`
    -   **Tests**: Extract `conversation_id`.
4.  **Send Message**
    -   **Endpoint**: `POST {{base_url}}/api/chat/send`
    -   **Body** (JSON):
        ```json
        {
            "message": "Explain the key concept.",
            "conversation_id": "{{conversation_id}}"
        }
        ```

### Test 4: Teacher Sequential RAG Chat
**Objective**: Stress test the RAG chat endpoint with a long sequence of follow-up questions in the same thread.

1.  **Login** (Teacher)
2.  **Create Conversation & Upload PDF** (Same steps as Test 2)
3.  **Chat Loop** (Sequence of requests)
    -   **Request 4a**: `POST .../chat` ("Summarize this")
    -   **Request 4b**: `POST .../chat` ("Explain point 1")
    -   **Request 4c**: `POST .../chat` ("Give examples")
    -   **Request 4d**: `POST .../chat` ("Create a quiz")
    -   *Note*: Ensure `thread_id` is preserved across requests.

### Test 5: Student Sequential Lesson Chat
**Objective**: Long conversation depth for a student session.

1.  **Login** (Student)
2.  **Start Lesson Chat**
3.  **Chat Loop** (Sequence of 5-10 messages) using `{{conversation_id}}`.

### Test 6: Repeated Ingest (Stability)
**Objective**: Verify system stability when user uploads the exact same file repeatedly.

1.  **Login**
2.  **Loop N Times** (using Postman Runner iterations or `setNextRequest` logic):
    -   Create Conversation
    -   Upload PDF (`{{doc_path}}`)
    -   Poll until verify success
    -   Assert that `chunks` count is consistent with previous runs.

### Test 7: RAG Pipeline Quality Benchmark
**Objective**: Accuracy/Quality check (more than load).

1.  **Login**
2.  **Iterate through a data files list** (Postman Runner used with a CSV data file containing `filename` and `expected_keywords`).
3.  **Upload PDF** (filename from CSV)
4.  **Ask Standard Question**: "Summarize key points."
5.  **Assert Response**: Check if response body contains `{{expected_keywords}}`.

---

## 4. Execution via Postman Runner

To simulate load:
1.  Open **Collection Runner**.
2.  Select the desired folder (e.g., "Test 2: Teacher Flow").
3.  **Iterations**: Set to `20` (simulates 20 users/runs).
4.  **Delay**: `100ms` (ramp-up).
5.  **Data File**: (Optional) Upload CSV with different user credentials to simulate unique users.
    -   CSV Format: `useremail,password`
    -   Use `{{useremail}}` variable in Login request.
6.  **Run**.
