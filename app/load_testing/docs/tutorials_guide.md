# Load Testing Tutorials & Workflows

This guide provides step-by-step tutorials for the most common testing workflows in the Iqbal AI ecosystem.

---

## Tutorial 1: The "Daily Smoke Test" (5 Minutes)
**Goal**: Verify that the authentication system and main dashboard are responsive under light load.

1.  **Prepare Assets**: Go to the **Assets** tab and ensure you have a "Teacher Set" with at least 5-10 users.
2.  **Configure**:
    *   **Test Type**: Select `Test 1: System Access`.
    *   **Concurrent Users**: Set to `5`.
3.  **Run**: Click **Start Load Test**.
4.  **Verify**:
    *   Watch for `User X logged in successfully` in the logs.
    *   Open the report once finished.
    *   **Success Criteria**: 100% success rate and < 2s average login time.

---

## Tutorial 2: Critical Path Audit (15 Minutes)
**Goal**: Validate the end-to-end "Teacher-to-Lesson" workflow under moderate pressure.

1.  **Prepare Assets**:
    *   **User Set**: A set of 5-10 Teachers.
    *   **Document Set**: A set containing a standard 5-10 page PDF.
    *   **Message CSV**: A CSV with at least 5 sample questions.
2.  **Configure**:
    *   **Test Type**: Select `Test 2: Teacher Flow`.
    *   **Concurrent Users**: Set to `5`.
    *   **Select Assets**: Link the User Set, Document Set, and CSV.
3.  **Run**: Click **Start Load Test**. This test involves ingestion, so it will take several minutes.
4.  **Verify**:
    *   Check for `Lesson Saved to DB` logs.
    *   **Success Criteria**: All "Primary Checks" in the report should be green.

---

## Tutorial 3: RAG Quality Benchmarking
**Goal**: Verify that AI responses are accurate and timely after a system update.

1.  **Prepare Assets**:
    *   **Document Set**: Your "Golden" document (one you know well).
    *   **Message CSV**: A CSV containing specific questions and expected "Keyword Hits".
2.  **Configure**:
    *   **Test Type**: Select `Test 8: RAG Quality`.
    *   **Assets**: Select your Golden Set and Question CSV.
3.  **Run**: Click **Start Load Test**. Note: This test runs sequentially (1 user) to ensure high-fidelity measurements.
4.  **Analyze**:
    *   Open the **AI Executive Summary**.
    *   Check the **Keyword Hits** metric to see how many responses contained your required terms.
    *   **Success Criteria**: High keyword hit ratio and "PASS" verdict from the AI analyst.

---

## Best Practices
- **Always start small**: Run a test with 1-2 users before jumping to 100.
- **Cool-down periods**: Wait 1 minute between heavy ingestion tests to let the Vector DB stabilize.
- **Sync Passwords**: If you change admin passwords, remember to click the **Sync Passwords** button in the User Set management UI (if available) or reload the set.
