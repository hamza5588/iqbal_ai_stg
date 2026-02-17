import time
import asyncio
import aiohttp
import logging
import random
import os
import json
import statistics
from typing import Callable, Any, List, Dict
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
    Execute Test 6: Same Document Uploaded N Times (Consistency/Stress).
    Flow:
    1. Select ONE document.
    2. Loop N times:
       - Create Conversation
       - Upload Same Document
       - Poll Status -> Record processing time, chunks
       - (Optional) Chat -> Record response
    3. Calculate consistency stats.
    """
    try:
        if not config.test_doc_set_id:
            msg = "Test 6 requires a test document set ID"
            log_func(msg, level="ERROR")
            summary.errors.append({"user": user.email, "error": msg})
            return

        # Fetch ONE random document to reuse
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

        iterations = config.requests_per_user or 5
        log_func(f"Starting repeated ingest test ({iterations} times) with {filename}...")
        
        processing_times = []
        chunk_counts = []
        thread_ids = []
        chat_responses = []

        for i in range(iterations):
            log_func(f"Iteration {i+1}/{iterations}...")
            
            # 1. Create Conversation
            create_conv_url = f"{config.base_url}/create_conversation"
            conversation_id = None
            async with session.post(create_conv_url, json={"title": f"Repeat Test {i+1}"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    conversation_id = data.get('conversation_id')
                    summary.total_requests += 1
                    summary.successful_requests += 1
                else:
                    summary.total_requests += 1
                    summary.failed_requests += 1
                    log_func(f"Failed to create conversation: {resp.status}", level="ERROR")
                    continue

            # 2. Upload PDF
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
                        continue
                else:
                    summary.failed_requests += 1
                    log_func(f"Upload HTTP error: {resp.status}", level="ERROR")
                    continue

            # 3. Poll Status
            if task_id:
                poll_url = f"{config.base_url}/api/rag/ingest/status/{task_id}"
                max_retries = 60
                retry_count = 0
                success = False
                
                while retry_count < max_retries:
                    async with session.get(poll_url) as resp:
                        summary.total_requests += 1
                        if resp.status == 200:
                            data = await resp.json()
                            status = data.get('status')
                            summary.successful_requests += 1
                            
                            if status == 'success':
                                thread_id = data.get('thread_id')
                                p_time = data.get('processing_time_seconds', 0)
                                chunks = data.get('chunks', 0)
                                
                                processing_times.append(float(p_time) if p_time else 0)
                                chunk_counts.append(int(chunks) if chunks else 0)
                                thread_ids.append(thread_id)
                                
                                log_func(f"Iter {i+1}: Success (Time: {p_time}s, Chunks: {chunks})")
                                success = True
                                break
                            elif status in ['failure', 'revoked']:
                                log_func(f"Iter {i+1}: Failed status {status}", level="ERROR")
                                break
                        else:
                            summary.failed_requests += 1
                    retry_count += 1
                    await asyncio.sleep(1)
                
                if not success:
                    log_func(f"Iter {i+1}: Timed out", level="ERROR")
                    continue

            # 4. Optional Chat Check (Consistency)
            if thread_id:
                chat_url = f"{config.base_url}/api/rag/chat"
                msg = "Summarize this document in one sentence."
                async with session.post(chat_url, json={
                    "message": msg,
                    "thread_id": thread_id,
                    "conversation_id": conversation_id
                }) as resp:
                    summary.total_requests += 1
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('success'):
                            chat_responses.append(data.get('message', ''))
                            summary.successful_requests += 1
                        else:
                            chat_responses.append("ERROR")
                            summary.failed_requests += 1
                    else:
                        chat_responses.append("HTTP_ERROR")
                        summary.failed_requests += 1

            # Cleanup thread/memory? usually we keep it for record, but delete_thread API exists
            # We skip deletion for now to allow manual inspection if needed

        # Calculate Stats
        if processing_times:
            avg_time = statistics.mean(processing_times)
            stdev_time = statistics.stdev(processing_times) if len(processing_times) > 1 else 0
            log_func(f"Processing Time: Avg={avg_time:.2f}s, Stdev={stdev_time:.2f}s")
            
            # Store in summary.errors (abuse slightly or add a new field)
            # We'll log it as a special INFO message with details
            stats = {
                "avg_processing_time": avg_time,
                "stdev_processing_time": stdev_time,
                "processing_times": processing_times,
                "chunk_counts": chunk_counts,
                "unique_chunk_counts": list(set(chunk_counts))
            }
            log_func("Detailed stats", details=stats)
            
            # Check consistency
            if len(set(chunk_counts)) > 1:
                log_func(f"WARNING: Inconsistent chunk counts: {set(chunk_counts)}", level="WARNING")
            else:
                log_func("Chunk count consistency: PASS")

    except Exception as e:
        log_func(f"Repeat ingest exception: {str(e)}", level="ERROR")
        summary.errors.append({"user": user.email, "error": str(e)})
