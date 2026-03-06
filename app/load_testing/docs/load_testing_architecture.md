# Iqbal AI: Load Testing Architecture & Technical Blueprint

This document serves as the authoritative, comprehensive technical manual for the Load Testing feature in Iqbal AI. It contains every business rule, code strategy, architectural dependency, and API endpoint required to understand, maintain, or entirely recreate the feature from scratch.

---

## 1. System Overview & Technology Stack

The Load Testing module is a native, self-contained performance suite embedded directly inside the Iqbal AI Administrator Dashboard. It is built to stress-test the application mimicking real user behavior across various APIs.

### Core Stack
* **Frontend:** Vue/React-less vanilla JS embedded in a monolithic Flask Jinja template (`templates/admin/load_testing.html`).
* **Backend Framework:** Python (Flask).
* **Asynchronous Engine:** `asyncio` combined with `aiohttp` for highly concurrent, non-blocking HTTP requests.
* **Execution Boundary:** Tests can be executed via distributed **Celery** workers (if `USE_CELERY_FOR_INGESTION=True`) or local **Python Background Threads** if Celery is unavailable.
* **Database:** SQLAlchemy ORM.
* **Visualizations:** `Chart.js` and `Marked.js` (for AI Markdown rendering) delivered via CDN.
* **AI Engine:** Integration with Groq / OpenAI via `app.models.ChatModel` for generating natural-language test analyses.

---

## 2. Core Database Models (`app/load_testing/models.py`)

The system relies on 8 primary SQLAlchemy models to persist state, assets, and historical records.

* `TestUserSet`: A bucket representing a group of auto-generated users.
* `TestUser`: Individual virtual credentials attached to a `TestUserSet` (Contains `email`, `role`, `password`).
* `TestDocumentSet`: A container for PDF assets.
* `TestDocument`: A physical PDF file reference attached to a `TestDocumentSet` (Contains `filename`, `file_path`, `size_bytes`).
* `TestMessageCSV`: Extraneous assets for custom standard queries.
* `LoadTestResult`: The master record of a test run. Stores start/end times, final `status`, raw JSON `metrics`, and the LLM `llm_analysis` payload.
* `LoadTestLog`: Persistent real-time logging records attached to a specific `LoadTestResult`.
* `LoadTestStatus`: Enum (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `STOPPED`).

---

## 3. Backend API Reference (`app/routes/load_test_routes.py`)

All endpoints are protected by the `@admin_only` decorator and prefixed with `/api/load-test`.

### Execution 
* `POST /start`: Initializes `LoadTestRunner`. Creates the `LoadTestResult` row and fires the background thread/Celery task.
* `POST /stop/<test_id>`: Flags the runner's `stop_requested` boolean to gracefully kill the `asyncio` event loop.
* `GET /status/<test_id>`: Polls numeric counters (active users, progress).
* `GET /status/<test_id>/logs`: Returns the latest terminal stdout strings from `LoadTestLog`.

### Reporting
* `GET /results`: Fetches the history of all tests.
* `DELETE /results/<test_id>`: Hard deletes a past run.
* `DELETE /results/all`: Purges the reporting dashboard.
* `GET /report/<test_id>/technical`: Computes and serves raw metrics (Latency arrays, P95, Medians).
* `POST /report/<test_id>/executive`: Triggers the LLM (Groq) to analyze the JSON trace and saves it to the DB.
* `GET /report/<test_id>/artifacts`: Returns the raw `metrics.get('artifacts')` array.
* `GET /artifact/download`: Converts a JSON artifact in memory to a downloadable `.md` or `.txt` blob.

### Asset Management
* `POST /users/create`: Generates N `TestUser` rows inside a `TestUserSet`.
* `GET /users` & `DELETE /users/<set_id>`
* `POST /doc-sets/create` & `POST /doc-sets/<set_id>/upload`: Handles multipart PDF saves.
* `GET /doc-sets` & `DELETE /doc-sets/<set_id>`
* `POST /message-csvs/upload` & `GET /message-csvs`

---

## 4. Asset Management Architecture

The load testing suite relies on physical and logical assets to execute tests. These assets are managed via three distinct managers inside `app/load_testing/`:

### 4.1. User Sets (`user_set_manager.py`)
Generates disposable testing credentials to mimic real users.
* **Logic:** When `create_user_set` is called, the system first creates a logical bucket `TestUserSet`. It then runs a loop up to the requested `count`.
* **Database Connection:** Crucially, it creates a *real* user record in the main application's `users` table, and then simultaneously creates a "shadow" `TestUser` record that links back via `real_user_id`. 
* **Collision Prevention:** Emails are formatted dynamically (e.g., `loadtest_{role}_{index}_{set_id}_{random_4_chars}@test.iqbalai.com`) to prevent unique constraint failures across multiple runs.
* **Passwords:** Passwords are intentionally saved in plain-text inside `TestUser` models because the load test engine (`runner.py`) actively reads them to perform automated REST API logins.

### 4.2. Document Sets (`document_set_manager.py`)
Manages physical PDFs used primarily for Teacher tests (Tests 2, 3, 5, 7, 8).
* **Logic:** A logical `TestDocumentSet` is created. Physical files uploaded to this set are routed through `secure_filename()` and saved directly to the server's disk space at `tests/assets/documents/<set_id>/<filename>`.
* **Database Connection:** A `TestDocument` row is created storing the physical OS file path and the calculated `file_size_bytes` (which is later converted to MB in reports).
* **Test Consumption:** During tests, Python scenarios execute queries against the `TestDocument` table filtering by the selected `doc_set_id`, usually grabbing a `random.choice(documents)` to simulate variance, except in Test 8 which loops over the *entire* set predictably.

### 4.3. Message CSVs (`message_csv_manager.py`)
Used to provide custom, diverse text prompts for AI Chat tests (Tests 4, 5, 6).
* **Logic:** Physical CSV files are saved to `tests/assets/message_csvs/<timestamp>_<filename>`. 
* **Encoding Strategy:** The manager automatically attempts to decode the file using a cascade of encodings (`utf-8`, `latin-1`, `cp1252`, `mac-roman`) to prevent crashes from Excel-exported files. It strips out binary objects like `.xlsx` ZIP headers.
* **Test Consumption:** `runner.py` reads the CSV file into Python memory *before* the asynchronous test loop starts. It converts the CSV into a standard Python `List[str]`. Inside the test sequence, virtual users index into this list to grab their chat Prompts.

---

## 5. The Execution Engine (`runner.py`)

The heart of the system is the `LoadTestRunner` class.

### Concurrency Strategy
The system does *not* spawn a physical OS thread per user. Instead, it spins up a single background thread which initializes an `asyncio` event loop.
Inside this loop, it uses `aiohttp.ClientSession` to fire off HTTP requests non-blockingly. `asyncio.gather()` is used to execute N "Worker" coroutines concurrently (simulating N users).

### State Management & Config
The state is passed via two dataclasses in `config.py`:
1. `LoadTestConfig`: Immutable. Contains user inputs (Concurrent Users, Iterations, Target URL).
2. `TestResultSummary`: Mutable. A shared state object modified actively by the `asyncio` workers. Tracks `successful_requests`, `failed_requests`, `rate_limit_hits`, `total_ingestion_time`, and arrays like `latency_trend` and `artifacts`.

### Safe Shutdown
To support immediate cancellation without memory leaks, every async while-loop in the system checks `if summary.stop_requested: break`.

---

## 5. The 8 Test Scenarios (Business Rules & Logic)

Each test lives in its own `.py` file inside `app/load_testing/scenarios/`.

| Test ID (Code Name) | Type | Code Logic & Architecture Strategy |
| :--- | :--- | :--- |
| **1 (`test_auth`)** | Concurrent | Simply hits the root `/` endpoint to confirm Flask renders the dashboard. Validates auth capacity. |
| **2/3 (`test_teacher_flow`)** | Concurrent | Maximum stress. `asyncio.gather` spawns N users. Each creates a conversation, uploads a PDF from the `TestDocumentSet` pool, and enters a 2s `asyncio.sleep` polling loop against the ingestion queue. Upon success, sends a single chat message. |
| **4 (`test_student_chat`)** | Concurrent | Swarm chat load. Logs into an existing lesson ID and fires POSTs to `/api/lessons/ask_question`. Records each response time. |
| **5 (`test_teacher_sequential`)** | Sequential | Single-user loop. Iterates through the `requests_per_user` counter. Uploads ONE document, then sequentially asks N questions. Validates AI context windows. |
| **6 (`test_student_sequential`)** | Sequential | Single-user loop. Same as Test 5, but for student lesson flows. |
| **7 (`test_teacher_repeat_ingest`)** | Sequential | Isolates vector-DB indexing memory leaks. Pushes the exact same physical PDF file through the `/api/rag/ingest` endpoint N consecutive times in a synchronous loop. |
| **8 (`test_rag_pipeline_quality`)** | Sequential | Bypasses speed tracking to evaluate intelligence. Iterates over *every* document in the selected `TestDocumentSet`. Asks a standard summary question, looks for keyword hits in the JSON response, and logs the string delta. |

---

## 6. Frontend Blueprint (`load_testing.html`)

The UI is a monolithic 2,400+ line Jinja template implementing the following dynamic systems using Vanilla JavaScript:

### Dynamic Parameter Rendering
The `updateFormFields()` JS function listens to the `<select id="test-type">` dropdown. It toggles CSS classes (`classList.add('hidden')`) to ensure users only see the exact form inputs required for the selected test (e.g., hiding Concurrent Users and forcefully setting it to `1` if a Sequential test is chosen).

### Smart Proxy Implementation
The load testing UI does not send traffic from the browser. It sends instructions to the Flask backend, which then attacks the "Target Environment". However, to bypass CORS when managing assets across environments, the `fetchWithProxy()` JS wrapper is used.

### Polling Mechanics
WebSockets/SSE are intentionally bypassed to simplify infrastructure. The UI uses standard `setInterval` polling:
* `updateLiveLogs()`: Fires every 2 seconds to fetch newly appended log strings.
* Auto-scrolls the terminal visually unless the user scrolls up manually.

### Reporting & Rendering
1. **Raw JSON to HTML**: When a report is clicked, the UI hits `/report/<id>/technical` to load the `TestResultSummary` JSON.
2. **Chart.js Injection**: The `renderChartsForTest()` function dynamically builds canvases. It looks at the test type to decide whether to render a line graph (for sequential data), a scatter/cloud plot, or a doughnut chart. It also automatically calculates a linear regression trendline for indexing delays.
3. **Artifact Injection**: Parses the `size_mb` metadata out of the `artifacts` array and manually assembles hyperlink HTML DOM nodes to point to `/api/load-test/artifact/download`.
4. **Marked.js**: Sanitizes and renders the LLM's raw markdown strings directly into stylized HTML divs.
