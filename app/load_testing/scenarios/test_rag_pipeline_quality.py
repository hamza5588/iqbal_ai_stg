import time
import asyncio
import aiohttp
import logging
import os
import json
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
    Execute Test 7: Multi-File RAG Pipeline Quality Benchmark.
    Flow:
    1. Iterate through ALL documents in the set.
    2. For each document: Upload -> Chat (Standard Question) -> Store Response.
    3. The collected responses will be analyzed by LLM in the reporting phase.
    """
    try:
        if not config.test_doc_set_id:
            msg = "Test 7 requires a test document set ID"
            log_func(msg, level="ERROR")
            summary.errors.append({"user": user.email, "error": msg})
            return

        # Fetch ALL documents in the set
        _session_factory = get_session_factory()
        db_session = _session_factory()
        documents = []
        try:
            documents = db_session.query(TestDocument).filter_by(doc_set_id=config.test_doc_set_id).all()
            if not documents:
                msg = f"No documents found in set {config.test_doc_set_id}"
                log_func(msg, level="ERROR")
                summary.errors.append({"user": user.email, "error": msg})
                return
            
            # Detach to use outside session
            db_session.expunge_all()
        finally:
            db_session.close()

        log_func(f"Starting RAG Quality Benchmark with {len(documents)} documents...")
        
        benchmark_results = []
        
        for i, doc in enumerate(documents):
            file_path = doc.file_path
            filename = doc.filename
            
            log_func(f"Processing doc {i+1}/{len(documents)}: {filename}...")
            
            if not os.path.exists(file_path):
                log_func(f"File not found: {file_path}", level="ERROR")
                summary.errors.append({"user": user.email, "error": f"File not found: {file_path}"})
                continue

            # 1. Create Conversation
            create_conv_url = f"{config.base_url}/create_conversation"
            conversation_id = None
            start_time = time.time()
            
            async with session.post(create_conv_url, json={"title": f"Benchmark {filename}"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    conversation_id = data.get('conversation_id')
                    summary.total_requests += 1
                else:
                    summary.total_requests += 1
                    summary.failed_requests += 1
                    log_func(f"Failed to create conv for {filename}: {resp.status}", level="ERROR")
                    continue

            # 2. Upload PDF
            ingest_url = f"{config.base_url}/api/rag/ingest"
            form_data = aiohttp.FormData()
            form_data.add_field('file', open(file_path, 'rb'), filename=filename, content_type='application/pdf')
            form_data.add_field('create_new_thread', 'true')
            form_data.add_field('conversation_id', str(conversation_id))
            
            task_id = None
            thread_id = None
            upload_start = time.time()
            
            async with session.post(ingest_url, data=form_data) as resp:
                summary.total_requests += 1
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        task_id = data.get('task_id')
                    else:
                        summary.failed_requests += 1
                        log_func(f"Upload failed for {filename}: {data.get('error')}", level="ERROR")
                        continue
                else:
                    summary.failed_requests += 1
                    log_func(f"Upload HTTP error for {filename}: {resp.status}", level="ERROR")
                    continue

            # 3. Poll
            processing_time = 0
            if task_id:
                poll_url = f"{config.base_url}/api/rag/ingest/status/{task_id}"
                max_retries = 60
                retry_count = 0
                
                while retry_count < max_retries:
                    async with session.get(poll_url) as resp:
                        summary.total_requests += 1
                        if resp.status == 200:
                            data = await resp.json()
                            status = data.get('status')
                            
                            if status == 'success':
                                thread_id = data.get('thread_id')
                                processing_time = data.get('processing_time_seconds', 0)
                                summary.successful_requests += 1
                                break
                            elif status in ['failure', 'revoked']:
                                log_func(f"Ingest failed for {filename}: {status}", level="ERROR")
                                break
                        else:
                            summary.failed_requests += 1
                    retry_count += 1
                    await asyncio.sleep(1)
                
                if not thread_id:
                    log_func(f"Ingest timed out for {filename}", level="ERROR")
                    continue

            # 4. Standard Question
            question = "Summarize the key points of this document in 3 bullet points."
            chat_url = f"{config.base_url}/api/rag/chat"
            ai_response = ""
            
            chat_start = time.time()
            async with session.post(chat_url, json={
                "message": question,
                "thread_id": thread_id,
                "conversation_id": conversation_id
            }) as resp:
                summary.total_requests += 1
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        ai_response = data.get('message', '')
                        summary.successful_requests += 1
                    else:
                        summary.failed_requests += 1
                        log_func(f"Chat failed for {filename}: {data.get('error')}", level="ERROR")
                else:
                    summary.failed_requests += 1
                    log_func(f"Chat HTTP error for {filename}: {resp.status}", level="ERROR")

            # Store result
            result_entry = {
                "filename": filename,
                "processing_time": processing_time,
                "response_length": len(ai_response),
                "response": ai_response,
                "status": "PASS" if ai_response else "FAIL"
            }
            benchmark_results.append(result_entry)
            log_func(f"Finished {filename}. Time: {processing_time}s. Response len: {len(ai_response)}")

        # Store benchmark results in logs (or metrics)
        # We can store strictly structured data in logs details
        log_func("Benchmark Results", details={"benchmark_data": benchmark_results})

    except Exception as e:
        log_func(f"Benchmark exception: {str(e)}", level="ERROR")
        summary.errors.append({"user": user.email, "error": str(e)})
