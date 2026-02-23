# Troubleshooting & FAQs

This document addresses common issues and frequently asked questions about the Iqbal AI Load Testing environment.

---

## 🛠 Troubleshooting Common Errors

### 1. "Cannot read properties of null (reading 'appendChild')"
- **Issue**: A JavaScript error occurred when clicking the executive summary.
- **Fix**: This was a known bug involving the trajectory chart container. It has been resolved in the current version. Ensure you are using the latest `load_testing.html` template.

### 2. "LLM Analysis unavailable: No API key provided"
- **Issue**: The Executive Summary button doesn't work or shows an error.
- **Fix**: Go to the **Settings** tab in the Admin Dashboard and ensure you have a valid `GROQ_API_KEY` saved in the environment or the current session.

### 3. Login Failures (Test 1 or others)
- **Issue**: Users are created but fail to log in during the test.
- **Check**:
    - Ensure the **Target Base URL** is correct (e.g., `http://localhost:5000` vs `http://127.0.0.1:5000`).
    - Verify that the users in your **User Set** actually exist in the main database.
    - If you recently changed passwords, the User Set might be using old data. Re-sync or recreate the user set.

### 4. Ingestion Timeouts
- **Issue**: Tests 2, 6, or 7 show "Timed Out" for document processing.
- **Fix**: 
    - Ingestion is CPU-intensive. If running 50+ concurrent users, the server may struggle. Reduce concurrency.
    - Ensure your PDF files are not excessively large (keep under 20MB for testing).
    - Checks the **Live Logs** to see if the RAG service returned a specific error code.

---

## ❓ Frequently Asked Questions

### Q: Does running a load test affect real users?
**A**: Yes, it can. Since these tests hit the real API and database, high-concurrency tests (100+) will slow down the application for regular teachers and students. **Always run heavy tests on Staging or during off-peak hours.**

### Q: Why is my Success Rate less than 100%?
**A**: A lower success rate is usually caused by:
1.  **Rate Limiting**: The AI provider (Groq/OpenAI) is throttling requests.
2.  **Concurrency Conflicts**: Two users trying to create the same lesson or upload the same file simultaneously.
3.  **Network Jitter**: Temporary connection drops.

### Q: Can I run multiple tests at once?
**A**: Technically yes, but not recommended. The results will be polluted by each other's traffic, making the performance charts difficult to read. Run one test at a time for the cleanest data.

### Q: How do I clear the test history?
**A**: Go to the **Results** tab and use the **Delete All Results** button for a full system cleanup, or delete individual runs using the trash icon. Note: This will also delete associated chat transcripts and lesson artifacts.

### Q: I added a new PDF to my document set, but the test doesn't see it?
**A**: Ensure you saved the Document Set after adding the file. The Runner loads the set configuration at the exact moment the test starts.
