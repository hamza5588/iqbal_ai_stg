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
    Execute Test 2: Multi-Teacher Document Upload Flow (Concurrent).
    Flow:
    1. Create Conversation
    2. Upload PDF (ingest)
    3. Poll status
    4. Chat with PDF
    5. Finalize Lesson
    6. Create Lesson
    """
    try:
        # 0. Get a document to upload
        if not config.test_doc_set_id:
            msg = "Test 2 requires a test document set ID"
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
            
            # Pick a random document (or cycled based on user index if we had access to it)
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

        # 1. Create Conversation
        log_func("Creating new conversation...")
        start_time = time.time()
        
        create_conv_url = f"{config.base_url}/create_conversation"
        conversation_id = None
        
        async with session.post(create_conv_url, json={"title": "Load Test Chat"}, headers={"Content-Type": "application/json"}) as resp:
            if resp.status == 200:
                data = await resp.json()
                conversation_id = data.get('conversation_id')
                log_func(f"Conversation created: {conversation_id}")
                summary.total_requests += 1
                summary.successful_requests += 1
            else:
                summary.total_requests += 1
                summary.failed_requests += 1
                log_func(f"Failed to create conversation: {resp.status}", level="ERROR")
                return

        # 2. Upload PDF
        log_func(f"Uploading file: {filename}...")
        ingest_url = f"{config.base_url}/api/rag/ingest"
        
        form_data = aiohttp.FormData()
        form_data.add_field('file', 
                           open(file_path, 'rb'),
                           filename=filename,
                           content_type='application/pdf')
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
                    # If sync processing (unlikely for chunks), thread_id might be here
                    thread_id = data.get('thread_id') 
                    log_func(f"Upload initiated. Task ID: {task_id}")
                    summary.successful_requests += 1
                else:
                    summary.failed_requests += 1
                    log_func(f"Upload returned success=False: {data.get('error')}", level="ERROR")
                    return
            else:
                summary.failed_requests += 1
                log_func(f"Upload failed: {resp.status}", level="ERROR")
                return

        # 3. Poll Status
        if task_id:
            log_func(f"Polling status for task {task_id}...")
            poll_url = f"{config.base_url}/api/rag/ingest/status/{task_id}"
            
            # Poll loop
            max_retries = 30 # 60 seconds max
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
                            processing_time = data.get('processing_time_seconds')
                            log_func(f"Ingest complete! Thread ID: {thread_id} ({processing_time}s)")
                            break
                        elif status == 'failure' or status == 'revoked':
                            summary.failed_requests += 1 # Logic failure
                            log_func(f"Ingest failed status: {status} - {data.get('error')}", level="ERROR")
                            return
                        else:
                            # Still processing
                            pass
                    else:
                        summary.failed_requests += 1
                        log_func(f"Poll check failed: {resp.status}", level="WARNING")
                
                retry_count += 1
                await asyncio.sleep(2)
            
            if retry_count >= max_retries:
                log_func("Ingest timed out", level="ERROR")
                summary.errors.append({"user": user.email, "error": "Ingest timed out"})
                return

        if not thread_id:
            log_func("No thread_id obtained", level="ERROR")
            return

        # 4. Chat with PDF
        log_func("Sending RAG chat message...")
        chat_url = f"{config.base_url}/api/rag/chat"
        payload = {
            "message": "Create a lesson plan based on this document.",
            "thread_id": thread_id,
            "conversation_id": conversation_id
        }
        
        async with session.post(chat_url, json=payload, headers={"Content-Type": "application/json"}) as resp:
            summary.total_requests += 1
            if resp.status == 200:
                data = await resp.json()
                if data.get('success'):
                    summary.successful_requests += 1
                    log_func("RAG chat success")
                else:
                    summary.failed_requests += 1
                    log_func(f"RAG chat logic failure: {data.get('error')}", level="ERROR")
            else:
                summary.failed_requests += 1
                log_func(f"RAG chat failed: {resp.status}", level="ERROR")

        # 5. Get Finalized Lesson Status
        # Usually checking this before finalized lesson creation
        log_func("Checking finalized lesson status...")
        finalized_url = f"{config.base_url}/api/rag/thread/{thread_id}/finalized-lesson"
        
        lesson_title = "Generated Lesson"
        lesson_content = "Default content"
        
        async with session.get(finalized_url) as resp:
            summary.total_requests += 1
            if resp.status == 200:
                data = await resp.json()
                summary.successful_requests += 1
                if data.get('success'):
                    lesson_title = data.get('lesson_title') or lesson_title
                    lesson_content = data.get('last_lesson_text') or lesson_content
            else:
                summary.failed_requests += 1
                log_func(f"Finalized lesson check failed: {resp.status}", level="WARNING")

        # 6. Create Lesson (Finalize)
        log_func("Saving lesson to database...")
        create_lesson_url = f"{config.base_url}/api/lessons/create"
        lesson_payload = {
            "title": f"{lesson_title} - LoadTest {time.time()}",
            "content": lesson_content,
            "focus_area": "General",
            "grade_level": "General",
            "summary": "Generated during load test"
        }
        
        async with session.post(create_lesson_url, json=lesson_payload, headers={"Content-Type": "application/json"}) as resp:
            summary.total_requests += 1
            if resp.status == 200:
                data = await resp.json()
                if data.get('success'):
                    summary.successful_requests += 1
                    log_func("Lesson saved successfully!")
                else:
                    summary.failed_requests += 1
                    log_func(f"Save lesson logic failure: {data.get('error')}", level="ERROR")
            else:
                summary.failed_requests += 1
                log_func(f"Save lesson failed: {resp.status}", level="ERROR")

    except Exception as e:
        log_func(f"Teacher flow exception: {str(e)}", level="ERROR")
        summary.errors.append({"user": user.email, "error": str(e)})
