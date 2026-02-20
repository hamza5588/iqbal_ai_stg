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
    log_func: Callable,
    messages: List[str] = None
):
    """
    Execute Test 7: Multi-File RAG Pipeline Quality Benchmark.
    Flow:
    1. Iterate through ALL documents in the set.
    2. For each document: Upload -> Chat (Standard Question) -> Store Response.
    3. The collected responses will be analyzed by LLM in the reporting phase.
    """
    scenario_start = time.time()
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
            
            log_func(f"[{user.email}] Processing doc {i+1}/{len(documents)}: {filename}")
            
            if not os.path.exists(file_path):
                log_func(f"[{user.email}] File not found on disk: {file_path}", level="ERROR")
                summary.errors.append({"user": user.email, "error": f"File not found: {file_path}"})
                continue

            # 1. Create Conversation
            create_conv_url = f"{config.base_url}/create_conversation"
            conversation_id = None
            start_conv = time.time()
            
            async with session.post(create_conv_url, json={"title": f"Benchmark {filename}"}) as resp:
                conv_duration = (time.time() - start_conv) * 1000
                summary.total_requests += 1 # Action 1: Create Conv
                if resp.status == 200:
                    data = await resp.json()
                    conversation_id = data.get('conversation_id')
                    log_func(f"[{user.email}] Conv created in {conv_duration:.0f}ms")
                else:
                    summary.failed_requests += 1
                    log_func(f"[{user.email}] Failed to create conv in {conv_duration:.0f}ms: {resp.status}", level="ERROR")
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
                upload_duration = (time.time() - upload_start) * 1000
                summary.total_requests += 1 # Action 2: Ingest Action
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        task_id = data.get('task_id')
                        thread_id = data.get('thread_id')
                        log_func(f"[{user.email}] Upload success for {filename} in {upload_duration:.0f}ms")
                    else:
                        summary.failed_requests += 1
                        log_func(f"[{user.email}] Upload logic fail for {filename} in {upload_duration:.0f}ms: {data.get('error')}", level="ERROR")
                        continue
                else:
                    summary.failed_requests += 1
                    log_func(f"[{user.email}] Upload HTTP error for {filename} in {upload_duration:.0f}ms: {resp.status}", level="ERROR")
                    continue

            # 3. Poll
            processing_time = 0
            if task_id:
                poll_url = f"{config.base_url}/api/rag/ingest/status/{task_id}"
                max_retries = 60
                retry_count = 0
                poll_start = time.time()
                log_func(f"[{user.email}] Ingest processing (Async)...")
                
                while retry_count < max_retries:
                    retry_count += 1
                    async with session.get(poll_url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            status = data.get('status')
                            
                            if status == 'success':
                                thread_id = data.get('thread_id')
                                processing_time = data.get('processing_time_seconds', 0)
                                summary.successful_requests += 1
                                log_func(f"[{user.email}] Ingest complete in {time.time() - poll_start:.1f}s")
                                break
                            elif status in ['failure', 'revoked']:
                                log_func(f"[{user.email}] Ingest failed in {time.time() - poll_start:.1f}s: {status}", level="ERROR")
                                break
                        else:
                            log_func(f"[{user.email}] Poll error {resp.status}", level="WARNING")
                    await asyncio.sleep(1)
                
                if not thread_id:
                    log_func(f"[{user.email}] Ingest timed out for {filename}", level="ERROR")
                    continue
            else:
                log_func(f"[{user.email}] Processing (Sync) completed during upload.")

            # 4. Standard Question (Iterative if messages provided)
            msg_list = messages if messages else ["Search the document for information about what to do if I didn't receive the verification email."]
            ai_response = ""
            
            for idx, question in enumerate(msg_list):
                log_func(f"[{user.email}] Sending question {idx+1}/{len(msg_list)}: \"{question[:30]}...\"")
                chat_url = f"{config.base_url}/api/rag/chat"
                
                chat_start = time.time()
                async with session.post(chat_url, json={
                    "message": question,
                    "thread_id": thread_id,
                    "conversation_id": conversation_id
                }) as resp:
                    chat_duration = time.time() - chat_start
                    summary.total_requests += 1 # Action: Quality Check Question
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('success'):
                            ai_response = data.get('answer', '') or data.get('message', '')
                            ret_score = data.get('retrieval_score')
                            summary.successful_requests += 1
                            
                            score_msg = f" (Confidence: {ret_score:.2f})" if ret_score is not None else ""
                            log_func(f"[{user.email}] Response {idx+1} received in {chat_duration:.1f}s{score_msg}")
                        else:
                            summary.failed_requests += 1
                            log_func(f"[{user.email}] Chat {idx+1} logic fail in {chat_duration:.1f}s: {data.get('error')}", level="ERROR")
                    else:
                        summary.failed_requests += 1
                        log_func(f"[{user.email}] Chat {idx+1} HTTP error in {chat_duration:.1f}s: {resp.status}", level="ERROR")

            # Store result
            result_entry = {
                "filename": filename,
                "processing_time": processing_time,
                "response_length": len(ai_response),
                "response": ai_response,
                "retrieval_score": ret_score if 'ret_score' in locals() else None,
                "status": "PASS" if ai_response else "FAIL"
            }
            benchmark_results.append(result_entry)

        # Store benchmark results in logs
        # Store benchmark results in logs
        total_bench_duration = time.time() - scenario_start
        
        # Calculate mean confidence
        conf_scores = [r.get('retrieval_score') for r in benchmark_results if r.get('retrieval_score') is not None]
        mean_conf = sum(conf_scores) / len(conf_scores) if conf_scores else 0.0
        
        log_func(f"Quality Benchmark Complete. Total Duration: {total_bench_duration:.1f}s. Mean Confidence: {mean_conf:.2f}", details={"benchmark_data": benchmark_results, "mean_confidence": mean_conf})

    except Exception as e:
        total_bench_duration = time.time() - scenario_start
        log_func(f"[{user.email}] Benchmark exception after {total_bench_duration:.1f}s: {str(e)}", level="ERROR")
        summary.errors.append({"user": user.email, "error": str(e)})
