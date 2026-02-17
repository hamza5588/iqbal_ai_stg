import time
import asyncio
import aiohttp
import logging
import random
import os
from typing import Callable, Any
from app.load_testing.config import LoadTestConfig, TestResultSummary
from app.load_testing.models import TestDocument, TestDocumentSet
from app.utils.db import get_session_factory

logger = logging.getLogger(__name__)

async def run(
    session: aiohttp.ClientSession, 
    user: Any, 
    config: LoadTestConfig, 
    summary: TestResultSummary, 
    log_func: Callable
):
    """
    Execute Test 4: Single Teacher Sequential RAG Chat (Stress Test).
    Flow:
    1. Create Conversation & Upload PDF (Once)
    2. Loop N times: Send chat message -> await response
    """
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
        log_func("Setting up RAG session...")
        
        # Create Conversation
        create_conv_url = f"{config.base_url}/create_conversation"
        conversation_id = None
        async with session.post(create_conv_url, json={"title": f"Stress Test {time.time()}"}) as resp:
            if resp.status == 200:
                data = await resp.json()
                conversation_id = data.get('conversation_id')
                summary.total_requests += 1
                summary.successful_requests += 1
            else:
                summary.total_requests += 1
                summary.failed_requests += 1
                log_func(f"Failed to create conversation: {resp.status}", level="ERROR")
                return

        # Upload PDF
        ingest_url = f"{config.base_url}/api/rag/ingest"
        form_data = aiohttp.FormData()
        form_data.add_field('file', open(file_path, 'rb'), filename=filename, content_type='application/pdf')
        form_data.add_field('create_new_thread', 'true')
        form_data.add_field('conversation_id', str(conversation_id))
        
        task_id = None
        thread_id = None
        
        async with session.post(ingest_url, data=form_data) as resp:
            summary.total_requests += 1
            if resp.status == 200:
                data = await resp.json()
                if data.get('success'):
                    task_id = data.get('task_id')
                    summary.successful_requests += 1
                else:
                    summary.failed_requests += 1
                    log_func(f"Upload failed: {data.get('error')}", level="ERROR")
                    return
            else:
                summary.failed_requests += 1
                log_func(f"Upload HTTP error: {resp.status}", level="ERROR")
                return

        # Poll Status
        if task_id:
            poll_url = f"{config.base_url}/api/rag/ingest/status/{task_id}"
            max_retries = 30
            retry_count = 0
            while retry_count < max_retries:
                async with session.get(poll_url) as resp:
                    summary.total_requests += 1
                    if resp.status == 200:
                        data = await resp.json()
                        status = data.get('status')
                        summary.successful_requests += 1
                        if status == 'success':
                            thread_id = data.get('thread_id')
                            log_func(f"Setup complete. Thread ID: {thread_id}")
                            break
                        elif status in ['failure', 'revoked']:
                            log_func(f"Ingest failed: {status}", level="ERROR")
                            return
                    else:
                        summary.failed_requests += 1
                retry_count += 1
                await asyncio.sleep(2)
            
            if not thread_id:
                log_func("Ingest timed out", level="ERROR")
                return

        # 2. Sequential Chat Loop
        requests_count = config.requests_per_user or 10
        log_func(f"Starting sequential chat loop ({requests_count} interactions)...")
        chat_url = f"{config.base_url}/api/rag/chat"
        
        chat_questions = [
            "Summarize the first page.",
            "What is the main argument?",
            "Explain the conclusion.",
            "List three key points.",
            "How does this relate to previous topics?",
        ]
        
        for i in range(requests_count):
            msg = chat_questions[i % len(chat_questions)]
            payload = {
                "message": f"[{i+1}/{requests_count}] {msg}",
                "thread_id": thread_id,
                "conversation_id": conversation_id
            }
            
            start_req = time.time()
            async with session.post(chat_url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                duration = time.time() - start_req
                summary.total_requests += 1
                
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        summary.successful_requests += 1
                        # log_func(f"Req {i+1}/{requests_count}: Success ({duration:.2f}s)")
                    else:
                        summary.failed_requests += 1
                        log_func(f"Req {i+1}/{requests_count}: Logic fail - {data.get('error')}", level="ERROR")
                else:
                    summary.failed_requests += 1
                    log_func(f"Req {i+1}/{requests_count}: HTTP {resp.status}", level="ERROR")
            
            # Small delay to be realistic? Or stress test implies minimal delay?
            # Config might have a delay parameter, but for stress test usually minimal.
            # await asyncio.sleep(0.1) 

        log_func(f"Sequential chat loop completed.")

    except Exception as e:
        log_func(f"Sequential test exception: {str(e)}", level="ERROR")
        summary.errors.append({"user": user.email, "error": str(e)})
