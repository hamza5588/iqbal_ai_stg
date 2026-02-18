# Load Testing System Walkthrough

## Overview
The Load Testing System is now fully implemented and integrated into the Admin Dashboard. It allows you to simulate various user behaviors (Teacher, Student) at scale to verify the performance of the Iqbal AI platform.

## Features
- **7 Test Scenarios**: Covering Auth, RAG ingestion, Chat, and End-to-End flows.
- **User Set Management**: Create and manage groups of test users (Students/Teachers).
- **Real-time Reporting**: View live test status and detailed technical reports.
- **AI Analysis**: Generate executive summaries of test results using the integrated LLM.

## How to Access
1. **Login** to the application as an Admin.
2. Navigate to the **Admin Dashboard**.
3. Click on the new **"Load Testing"** tab in the sidebar (bottom left).

## Step-by-Step Guide

### 1. Create Test Users
Before running a test, you need a set of users to simulate.
- Go to the **"Test Assets"** tab.
- Click **"+ Create Users"**.
- Enter a name (e.g., "Standard Class"), select a Role (Student/Teacher), and the count (e.g., 5).
- Click **Create**. The system will generate these users in the database.

### 2. Run a Load Test
- Go to the **"Run Tests"** tab.
- **Test Scenario**: Select one of the 7 available scenarios.
  - *Recommendation*: Start with "Test 1: Multi-User Sign-In" to verify basic connectivity.
- **Target Environment**: Select "Localhost" (for local testing) or "Staging".
- **Concurrent Users**: Set the number of simulated users (must be <= User Set count).
- **User Set**: Select the user set created in Step 1.
- **Scenario Specifics**:
  - For **Teacher/RAG tests** (Tests 2, 4, 6, 7), ensure you have the `TestDocumentSet` ID (currently managed via DB/Shell, default is 1).
  - For **Student Chat tests** (Tests 3, 5), enter a valid `Lesson ID` (e.g., from the Lessons table).
- Click **"Start Test"**.

### 3. Analyze Results
- Once started, the test will appear in the **"Active Tests"** panel.
- Go to the **"Results & Reports"** tab to see the history.
- Click **"View"** on any result to see details.
- **Technical Report**: JSON format with success rates, RPS, and error logs.
- **AI Executive Summary**: Click the purple button to get an LLM-generated analysis of the performance.

## Implementation Details
- The system uses `app/load_testing/config.py` for core settings.
- The runner is asynchronous (`app/load_testing/runner.py`) using `aiohttp` for high concurrency.
