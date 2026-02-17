import time
import aiohttp
import logging
import random
from typing import Callable, Any
from app.load_testing.config import LoadTestConfig, TestResultSummary

logger = logging.getLogger(__name__)

# Same sample questions, maybe larger pool
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
    "Tell me more about the first topic.",
    "Why is this important?",
    "Who is the main figure here?",
    "When did this happen?",
    "Where did this take place?",
]

async def run(
    session: aiohttp.ClientSession, 
    user: Any, 
    config: LoadTestConfig, 
    summary: TestResultSummary, 
    log_func: Callable
):
    """
    Execute Test 5: Single Student Sequential Lesson Chat (Stress Test).
    Flow:
    1. Loop N times: Send question -> await response
    """
    if not config.lesson_id:
        msg = "Test 5 requires a valid lesson_id"
        log_func(msg, level="ERROR")
        summary.errors.append({"user": user.email, "error": msg})
        return

    requests_count = config.requests_per_user or 10
    log_func(f"Starting sequential student chat loop ({requests_count} interactions)...")
    url = f"{config.base_url}/api/lessons/ask_question"
    
    for i in range(requests_count):
        question = SAMPLE_QUESTIONS[i % len(SAMPLE_QUESTIONS)]
        # Add index to make it unique?
        question = f"[{i+1}/{requests_count}] {question}"
        
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
                        # log_func(f"Q {i+1}/{requests_count}: Answer received ({duration:.2f}s)")
                    else:
                        summary.failed_requests += 1
                        log_func(f"Q {i+1}/{requests_count}: Empty answer", level="ERROR")
                else:
                    summary.failed_requests += 1
                    log_func(f"Q {i+1}/{requests_count}: Failed {resp.status}", level="ERROR")
                    
        except Exception as e:
            log_func(f"Student sequential exception: {str(e)}", level="ERROR")
            summary.errors.append({"user": user.email, "error": str(e)})
            
    log_func("Sequential student chat loop completed.")
