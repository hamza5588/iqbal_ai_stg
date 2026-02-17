import time
import aiohttp
import logging
import random
from typing import Callable, Any
from app.load_testing.config import LoadTestConfig, TestResultSummary

logger = logging.getLogger(__name__)

# Pool of sample questions to simulate student queries
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
    log_func: Callable
):
    """
    Execute Test 3: Multi-Student Lesson Chat (Concurrent).
    Flow:
    1. Send a question to the specified lesson
    """
    if not config.lesson_id:
        msg = "Test 3 requires a valid lesson_id"
        log_func(msg, level="ERROR")
        summary.errors.append({"user": user.email, "error": msg})
        return

    # Pick a random question
    question = random.choice(SAMPLE_QUESTIONS)
    
    log_func(f"Asking question: '{question}'...")
    url = f"{config.base_url}/api/lessons/ask_question"
    
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
                    summary.successful_requests += 1
                    log_func(f"Answer received ({len(answer)} chars) in {duration:.2f}s")
                else:
                    summary.failed_requests += 1
                    log_func("Answer was empty", level="ERROR")
            else:
                summary.failed_requests += 1
                log_func(f"Question failed: {resp.status}", level="ERROR")
                try:
                    error_data = await resp.json()
                    log_func(f"Error details: {error_data}", level="ERROR")
                except:
                    pass

    except Exception as e:
        log_func(f"Student chat exception: {str(e)}", level="ERROR")
        summary.errors.append({"user": user.email, "error": str(e)})
