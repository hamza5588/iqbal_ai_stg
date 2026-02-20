import time
import asyncio
import aiohttp
import logging
import random
from typing import Callable, Any, List
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
    log_func: Callable,
    messages: List[str] = None
):
    """
    Execute Test 5: Single Student Sequential Lesson Chat (Stress Test).
    Flow:
    1. Loop N times: Send question -> await response
    """
    scenario_start = time.time()
    if not config.lesson_id:
        msg = "Test 5 requires a valid lesson_id"
        log_func(msg, level="ERROR")
        summary.errors.append({"user": user.email, "error": msg})
        return

    msg_list = messages if messages else SAMPLE_QUESTIONS
    # Limit to config.requests_per_user if set
    if config.requests_per_user and config.requests_per_user > 0:
        msg_list = msg_list[:config.requests_per_user]
    requests_per_user = len(msg_list)
    
    log_func(f"[{user.email}] Starting student chat for {requests_per_user} messages...")
    url = f"{config.base_url}/api/lessons/ask_question"
    
    for i, question in enumerate(msg_list):
        if summary.stop_requested:
            log_func(f"[{user.email}] Chat sequence stopped by user")
            break
        log_func(f"[{user.email}] Sending message {i+1}/{requests_per_user}: \"{question[:50]}...\"")
        
        payload = {
            "lesson_id": config.lesson_id,
            "question": question
        }
        
        start_time = time.time()
        try:
            async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                duration = time.time() - start_time
                summary.total_requests += 1
                if resp.status == 429:
                    summary.rate_limit_hits += 1
                if resp.status == 200:
                    data = await resp.json()
                    answer = data.get('answer', '')
                    if answer:
                        summary.messages_sent += 1
                        summary.successful_requests += 1
                        summary.latency_trend.append(duration)
                        ans_snippet = answer[:50].replace('\n', ' ')
                        log_func(f"[{user.email}] Response {i+1} received in {duration:.1f}s: \"{ans_snippet}...\"")
                    else:
                        summary.failed_requests += 1
                        log_func(f"[{user.email}] Chat {i+1} empty response in {duration:.1f}s", level="ERROR")
                else:
                    summary.failed_requests += 1
                    log_func(f"[{user.email}] Chat {i+1} HTTP error {resp.status} in {duration:.1f}s", level="ERROR")
                    
        except Exception as e:
            log_func(f"[{user.email}] student chat exception: {str(e)}", level="ERROR")
            summary.errors.append({"user": user.email, "error": str(e)})
            
        await asyncio.sleep(0.5)
            
    total_user_duration = time.time() - scenario_start
    log_func(f"[{user.email}] Student Test Complete. Total Duration: {total_user_duration:.1f}s")
