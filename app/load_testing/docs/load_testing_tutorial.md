# Load Testing: A Step-by-Step Guide for Beginners

Welcome to the Load Testing Console! This tool helps you understand how well the application performs when multiple people try to use it at the exact same time. 

Think of it like a stress test for a physical building: if one person walks through the door, everything is fine. But what happens if 500 people try to shove through the door simultaneously? Does the door break? Does a line form around the block?

This tool answers those questions for our digital features by simulating "virtual users" to see if the system stays fast and reliable under pressure.

---

## 🧭 Step 1: Managing Assets (The Starting Materials)

Before you can run a simulation, your virtual users need things to work with. These are called **Assets**, and you create them in the **Test Assets** tab.

### Creating Virtual Users (User Sets)
You do **not** need to upload spreadsheets of users! The system builds them for you.
1. Click the **Create Users** button.
2. Give the set a recognizable name (e.g., "50_Math_Teachers").
3. Choose the **Role**: **Teacher** or **Student**.
4. Enter the **Count** (how many virtual people you want to create, up to 1,000).
5. Provide a shared **Password** they will all use to log in.
6. Click **Create**. The system will automatically create real accounts in the database for these virtual users to use during the test.

### Creating Document Sets (For Teacher Tests)
If a test requires teachers to upload PDFs, you need to provide the PDFs.
1. Click **Create Doc Set**.
2. Give it a name (e.g., "Biology_PDFs") and click **Create**.
3. Once created, upload the actual PDF files into this set. During the test, virtual teachers will randomly grab PDFs from this bucket to upload.

### Message CSVs (For Chat Tests)
For tests where users chat with the AI, you can either select the **"Default Messages"** built into the system, or click **Upload Message CSV** to provide a custom list of questions for the virtual users to ask.

---

## 🧪 Step 2: Choosing Your Test & Setting Parameters

Go to the **Run Tests** tab. You will see a dropdown menu of different test scenarios. Depending on which test you pick, the interface will automatically show or hide specific settings to match the test's requirements.

Here is a comprehensive breakdown of every test, exactly how it works under the hood, and what parameters you need to provide:

### Test 1: System Access (Auth & Dashboard)
* **What it does:** Simulates a massive crowd of users logging in and requesting the main dashboard at the exact same second to verify that the authentication servers don't crash.
* **Required Parameters:** `User Set`, `Concurrent Users`.
* **Flow:** The system logs in every user concurrently and requests the main application homepage, checking for a successful welcome message.

### Test 2 & 3: The "Teacher's Heart" (Concurrent Flow)
* **What it does:** Simulates multiple teachers performing the hardest task in the app: uploading PDFs, waiting for the AI to read them, and creating a lesson—all simultaneously.
* **Required Parameters:** `User Set`, `Document Set`, `Concurrent Users`. Optional: `Message CSV`.
* **Flow:** Every virtual teacher spins up, creates a blank conversation, randomly grabs a PDF from your Document Set and uploads it. They sit and poll the server until the document is finished processing. Then, they send one chat message and click "Finalize Lesson".

### Test 4: The "Virtual Classroom" (Concurrent Student Chat)
* **What it does:** Takes hundreds of logged-in students and forces them to all send chat messages to an AI lesson simultaneously.
* **Required Parameters:** `User Set`, `Lesson ID` (must exist in the target environment), `Concurrent Users`. Optional: `Message CSV`.
* **Flow:** Virtual students navigate to the Lesson ID provided. They will grab a question from your CSV (or the default list) and send it. *Note: They will run until they have sent all messages in the CSV, or until you hit the maximum "Messages to Send" setting.*

### Test 5: Deep Research (Teacher Long Chat)
* **What it does:** Simulates a teacher performing a very long, deep chat session about a single document.
* **Required Parameters:** `User Set` (Only uses 1 user), `Document Set`, `Messages to Send`. Optional: `Message CSV`.
* **Flow:** This is a **Sequential Test**. The system locks the user count to `1`. The single teacher uploads one random document and waits. Once complete, they send a single message and *wait* for the AI to fully answer before sending the next one. They repeat this for the total number of "Messages to Send". Useful for testing AI memory/context limits.

### Test 6: Intense Study Session (Student Deep Q&A)
* **What it does:** Identical to Test 5, but for students drilling deep into a single existing lesson rather than uploading a document.
* **Required Parameters:** `User Set` (Only uses 1 user), `Lesson ID`, `Messages to Send`. Optional: `Message CSV`.
* **Flow:** A **Sequential Test**. A single student navigates to the Lesson ID and sequentially asks a chain of questions, waiting for each answer. 

### Test 7: Ingest System Reliability (Repeated Processing)
* **What it does:** Uploads the *exact same* document over and over again to stress-test the vector database indexing. 
* **Required Parameters:** `User Set` (Only uses 1 user), `Document Set`, `Upload Iterations`. 
* **Flow:** A **Sequential Test**. The script grabs ONE document from your set. It creates a conversation, uploads the document, waits for it to finish parsing, and then throws the conversation away. It immediately repeats this upload process with the exact same file for the number of "Upload Iterations" you specify. This is the best way to find memory leaks.

### Test 8: IQ (RAG Quality) Benchmark
* **What it does:** A slow, methodical test to verify how *smart* the AI is, ensuring a new code update didn't break its reading comprehension.
* **Required Parameters:** `User Set` (Only uses 1 user), `Document Set`.
* **Flow:** A **Sequential Test**. The script fetches *every single document* in your Document Set. One by one, it uploads the document, waits for it to process, and asks it a standard question: "Summarize the key points of this document in 3 bullet points." It stores the AI's exact text response and moves to the next file until all files in the set are processed.

---

## 🏃‍♀️ Step 3: Running the Test

When you click **Start Load Test**, the system begins spinning up the virtual users. 

**Live Logs:** As the test runs, a black terminal window will appear at the bottom. This is the "Live Log." It's a real-time play-by-play of what the virtual users are doing. You'll see things like:
> *[user1@test.com] Uploading physics_lesson.pdf...*
> *[user1@test.com] Ingest complete in 4.2s (1.8MB)*
> *[user1@test.com] Sending message 1: "What is velocity?"*

---

## 📊 Step 4: Reading the Reports

When a test finishes, switch to the **Results & Reports** tab and click **View Report**. A "Result Details" window will pop up with three main tabs:

### 1. The Summary Tab (Technical Details)
This is what loads first. It is a strict numerical breakdown for power-users.
* **Metrics:** Shows total successful requests vs. failed requests, rate limit strikes (HTTP 429), and data transferred.
* **P95 Latency:** You'll see "Median" and "95th Percentile" response times. If the Median is 1 second, but the P95 is 20 seconds, it means most people had a fast experience, but the unluckiest 5% of your users got completely stuck waiting.

### 2. AI Executive Summary (with Charts)
If you click the **"AI Executive Summary"** button at the bottom of the window, a built-in AI assistant reads the raw metrics data and writes a plain-English summary of what happened. It will categorize the test as a pass or fail, point out major bottlenecks, and suggest what the engineering team should fix.

Scroll down past the AI text, and you will see **Visual Charts**. These are interactive graphs dynamically generated based on the test type:
* **The Response Quality Breakdown:** A doughnut chart showing successful vs. failed clicks.
* **Sequential Trend Lines (Tests 5, 6, 7):** If you ran a sequential test, you'll see a line graph comparing action 1 to action 100. If the line angles upward over time, your server is struggling to clean up memory and gets slower the longer people use it. Note: This chart includes an automatic "trend line" calculated via linear regression.
* **Global Latency Cloud (Tests 2, 4):** For concurrent tests, it plots how long it took to process documents or answer questions as hundreds of users hit the server at once.

### 3. Artifacts Tab
Click the "Artifacts" tab at the top of the window. "Artifacts" are the physical proof left behind by the virtual users. 
* **Chat Transcripts:** If a virtual user chatted with the AI during Test 4 or 6, their entire conversation history is saved here.
* **Extracted Text:** If a virtual teacher uploaded a PDF in Test 2, 7, or 8, the report saves a snapshot of the final document exactly as the backend database recorded it.
* **File Metadata:** The UI will display the name of the document and nicely render the parsed file size in grey beneath it (e.g., `biology_notes.pdf` `<br>` `• 1.8 MB`) so you can directly correlate long ingestion times with the file size that caused them.
