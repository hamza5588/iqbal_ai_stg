import time
import asyncio
import aiohttp
import logging
import random
from typing import Callable, Any, List
from app.load_testing.config import LoadTestConfig, TestResultSummary

logger = logging.getLogger(__name__)

# Fallback sample questions when no CSV is provided
SAMPLE_QUESTIONS = [
    "Can you explain the main concept?",
    "What are the key takeaways from this?",
    "I don't understand the second paragraph.",
    "Can you give me an example?",
    "How does this relate to real life?",
    "Summarize this for me.",
    "What is the most important point?",
    "Is this related to what we learned last week?",
    "Can you simplify the explanation?",
    "What comes next?",
]

async def run(
    session: aiohttp.ClientSession, 
    user: Any, 
    config: LoadTestConfig, 
    summary: TestResultSummary, 
    log_func: Callable,
    messages: List[str] = None
):
    """
    Execute Test 3: Multi-Student Lesson Chat (Concurrent).

    Each student iterates through ALL messages from the CSV sequentially:
      send msg 1 → wait for response → send msg 2 → wait → ... → done

    Multiple students run concurrently via asyncio.gather in runner.py,
    so each student proceeds to their next message as soon as their own
    response arrives — no waiting for other students.
    """
    scenario_start = time.time()
    if not config.lesson_id:
        msg = "Test 3 requires a valid lesson_id"
        log_func(msg, level="ERROR")
        summary.errors.append({"user": user.email, "error": msg})
        return

    msg_list = messages if messages else SAMPLE_QUESTIONS
    total_messages = len(msg_list)

    log_func(f"[{user.email}] Starting student chat — {total_messages} messages to send")
    url = f"{config.base_url}/api/lessons/ask_question"

    for i, question in enumerate(msg_list):
        log_func(f"[{user.email}] Sending message {i+1}/{total_messages}: \"{question[:60]}\"")

        payload = {
            "lesson_id": config.lesson_id,
            "question": question
        }

        start_time = time.time()
        try:
            async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                duration = time.time() - start_time
                summary.total_requests += 1

                if resp.status == 200:
                    data = await resp.json()
                    answer = data.get('answer', '')
                    if answer:
                        summary.messages_sent += 1
                        summary.successful_requests += 1
                        ans_snippet = answer[:50].replace('\n', ' ')
                        log_func(f"[{user.email}] Response {i+1} received in {duration:.1f}s: \"{ans_snippet}...\"")
                    else:
                        summary.failed_requests += 1
                        log_func(f"[{user.email}] Message {i+1} empty response in {duration:.1f}s", level="ERROR")
                else:
                    summary.failed_requests += 1
                    log_func(f"[{user.email}] Message {i+1} HTTP {resp.status} in {duration:.1f}s", level="ERROR")

        except Exception as e:
            log_func(f"[{user.email}] Message {i+1} exception: {str(e)}", level="ERROR")
            summary.errors.append({"user": user.email, "error": str(e)})

        # Small delay between messages to avoid hammering
        await asyncio.sleep(0.5)

    total_duration = time.time() - scenario_start
    log_func(f"[{user.email}] Student Chat Complete — {total_messages} messages in {total_duration:.1f}s")
