import time
import asyncio
import aiohttp
import logging
import random
import os
from typing import Callable, Any, List
from app.load_testing.config import LoadTestConfig, TestResultSummary
from app.load_testing.models import TestDocument, TestDocumentSet
from app.utils.db import get_session_factory

logger = logging.getLogger(__name__)

async def run(
    session: aiohttp.ClientSession, 
    user: Any, 
    config: LoadTestConfig, 
    summary: TestResultSummary, 
    log_func: Callable,
    messages: List[str] = None
):
    """
    Execute Test 4: Single Teacher Sequential RAG Chat (Stress Test).
    Flow:
    1. Create Conversation & Upload PDF (Once)
    2. Loop N times: Send chat message -> await response
    """
    scenario_start = time.time()
    try:
        # 0. Get a document to upload
        if not config.test_doc_set_id:
            msg = "Test 4 requires a test document set ID"
            log_func(msg, level="ERROR")
            summary.errors.append({"user": user.email, "error": msg})
            return

        # Fetch random document from the set
        _session_factory = get_session_factory()
        db_session = _session_factory()
        try:
            documents = db_session.query(TestDocument).filter_by(doc_set_id=config.test_doc_set_id).all()
            if not documents:
                msg = f"No documents found in set {config.test_doc_set_id}"
                log_func(msg, level="ERROR")
                summary.errors.append({"user": user.email, "error": msg})
                return
            
            doc_record = random.choice(documents)
            file_path = doc_record.file_path
            filename = doc_record.filename
            
            if not os.path.exists(file_path):
                msg = f"File not found on disk: {file_path}"
                log_func(msg, level="ERROR")
                summary.errors.append({"user": user.email, "error": msg})
                return
                
        finally:
            db_session.close()

        # 1. Setup: Create Conversation & Upload PDF
        log_func(f"[{user.email}] Step 1: Setting up conversation...")
        
        # Create Conversation
        create_conv_url = f"{config.base_url}/create_conversation"
        conversation_id = None
        start_setup = time.time()
        
        async with session.post(create_conv_url, json={"title": f"Stress Test {time.time()}"}) as resp:
            setup_duration = (time.time() - start_setup) * 1000
            summary.total_requests += 1 # Action 1: Create Conversation
            if resp.status == 429:
                summary.rate_limit_hits += 1
            if resp.status == 200:
                data = await resp.json()
                conversation_id = data.get('conversation_id')
                summary.successful_requests += 1
                log_func(f"[{user.email}] Conv created: {conversation_id} in {setup_duration:.0f}ms")
            else:
                summary.total_requests += 1
                summary.failed_requests += 1
                log_func(f"[{user.email}] Failed to create conv: {resp.status} in {setup_duration:.0f}ms", level="ERROR")
                return

        # Upload PDF
        log_func(f"[{user.email}] Step 2: Uploading {filename}...")
        ingest_url = f"{config.base_url}/api/rag/ingest"
        form_data = aiohttp.FormData()
        form_data.add_field('file', open(file_path, 'rb'), filename=filename, content_type='application/pdf')
        form_data.add_field('create_new_thread', 'true')
        form_data.add_field('conversation_id', str(conversation_id))
        
        task_id = None
        thread_id = None
        upload_start = time.time()
        
        async with session.post(ingest_url, data=form_data) as resp:
            upload_duration = (time.time() - upload_start) * 1000
            summary.total_requests += 1 # Action 2: Ingest Action
            if resp.status == 429:
                summary.rate_limit_hits += 1
            if resp.status == 200:
                data = await resp.json()
                if data.get('success'):
                    task_id = data.get('task_id')
                    summary.total_file_size_mb += os.path.getsize(file_path) / (1024 * 1024)
                    summary.successful_requests += 1
                    log_func(f"[{user.email}] Upload success in {upload_duration:.0f}ms")
                else:
                    summary.failed_requests += 1
                    log_func(f"[{user.email}] Upload logic fail in {upload_duration:.0f}ms: {data.get('error')}", level="ERROR")
                    return
            else:
                summary.failed_requests += 1
                log_func(f"[{user.email}] Upload HTTP error in {upload_duration:.0f}ms: {resp.status}", level="ERROR")
                return

        # Poll Status
        if task_id:
            log_func(f"[{user.email}] Step 3: Ingestion processing (Async)...")
            poll_url = f"{config.base_url}/api/rag/ingest/status/{task_id}"
            max_retries = 30
            retry_count = 0
            poll_start = time.time()
            while retry_count < max_retries:
                if summary.stop_requested:
                    log_func(f"[{user.email}] Ingest poll stopped by user")
                    return
                retry_count += 1
                async with session.get(poll_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        status = data.get('status')
                        if status == 'success':
                            thread_id = data.get('thread_id')
                            summary.total_ingestion_time += time.time() - poll_start
                            log_func(f"[{user.email}] Setup complete in {time.time() - poll_start:.1f}s")
                            
                            # Add extracted text artifact metadata
                            summary.artifacts.append({
                                "user_email": user.email,
                                "type": "extracted_text",
                                "thread_id": thread_id,
                                "doc_name": filename
                            })
                            break
                        elif status in ['failure', 'revoked']:
                            log_func(f"[{user.email}] Ingest failed in {time.time() - poll_start:.1f}s: {status}", level="ERROR")
                            return
                    else:
                        log_func(f"[{user.email}] Poll error {resp.status}", level="WARNING")
                await asyncio.sleep(2)
            
            if not thread_id:
                log_func(f"[{user.email}] Ingest timed out in {time.time() - poll_start:.1f}s", level="ERROR")
                return
        else:
            log_func(f"[{user.email}] Step 3: Processing (Synchronous) completed during Step 2.")

        # 2. Sequential Chat Loop
        msg_list = messages if messages else ["Hello, summarize this."]
        # Limit to config.requests_per_user if set
        if config.requests_per_user and config.requests_per_user > 0:
            msg_list = msg_list[:config.requests_per_user]
        requests_per_user = len(msg_list)
        
        log_func(f"[{user.email}] Starting sequential chat loop for {requests_per_user} messages...")
        chat_url = f"{config.base_url}/api/rag/chat"
        
        chat_transcript = []
        for i, msg in enumerate(msg_list):
            if summary.stop_requested:
                log_func(f"[{user.email}] Sequential loop stopped by user")
                break
            payload = {
                "message": msg,
                "thread_id": thread_id,
                "conversation_id": conversation_id
            }
            
            log_func(f"[{user.email}] Sending message {i+1}/{requests_per_user}: \"{msg[:50]}...\"")
            start_req = time.time()
            async with session.post(chat_url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                duration = time.time() - start_req
                summary.total_requests += 1 # Each chat message is an Action
                if resp.status == 429:
                    summary.rate_limit_hits += 1
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        summary.messages_sent += 1
                        summary.successful_requests += 1
                        summary.latency_trend.append(duration)
                        ans_text = data.get('answer') or data.get('message') or ""
                        ans_snippet = ans_text[:50].replace('\n', ' ')
                        log_func(f"[{user.email}] Response {i+1} received in {duration:.1f}s: \"{ans_snippet}...\"")
                        
                        # Add to transcript
                        chat_transcript.append({"role": "user", "content": msg})
                        chat_transcript.append({"role": "bot", "content": ans_text, "latency": duration})
                    else:
                        summary.failed_requests += 1
                        log_func(f"[{user.email}] Chat {i+1} logic fail in {duration:.1f}s: {data.get('error')}", level="ERROR")
                else:
                    summary.failed_requests += 1
                    log_func(f"[{user.email}] Chat {i+1} HTTP error {resp.status} in {duration:.1f}s", level="ERROR")
            
            await asyncio.sleep(0.5) 

        total_user_duration = time.time() - scenario_start
        log_func(f"[{user.email}] Sequential Test Complete. Total Duration: {total_user_duration:.1f}s")
        
        # Add chat_transcript artifact metadata (Sequential tests don't have lessons at the end)
        summary.artifacts.append({
            "user_email": user.email,
            "type": "chat_transcript",
            "conversation_id": conversation_id,
            "doc_name": filename,
            "transcript": chat_transcript
        })

    except Exception as e:
        total_user_duration = time.time() - scenario_start
        log_func(f"[{user.email}] Sequential test exception after {total_user_duration:.1f}s: {str(e)}", level="ERROR")
        summary.errors.append({"user": user.email, "error": str(e)})
