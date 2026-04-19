import time
import asyncio
import aiohttp
import logging
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
    log_func: Callable,
    messages: List[str] = None
):
    """
    Execute Test 8: Multi-File RAG Pipeline Quality Benchmark.
    Flow:
    1. Iterate through ALL documents in the set.
    2. For each document: Upload -> Chat (Standard Question) -> Store Response.
    3. The collected responses will be analyzed by LLM in the reporting phase.
    """
    scenario_start = time.time()
    processing_times = []
    try:
        if not config.test_doc_set_id:
            msg = "Test 8 requires a test document set ID"
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
            if summary.stop_requested:
                log_func(f"[{user.email}] Benchmark stopped by user")
                break
            file_path = doc.file_path
            
            if not os.path.exists(file_path):
                log_func(f"[{user.email}] File not found on disk: {file_path}", level="ERROR")
                summary.errors.append({"user": user.email, "error": f"File not found: {file_path}"})
                continue

            # 1. Create Conversation
            create_conv_url = f"{config.base_url}/create_conversation"
            conversation_id = None
            start_conv = time.time()
            
            async with session.post(create_conv_url, json={"title": f"Benchmark {doc.filename}"}) as resp:
                conv_duration = (time.time() - start_conv) * 1000
                summary.total_requests += 1 # Action 1: Create Conv
                if resp.status == 429:
                    summary.rate_limit_hits += 1
                if resp.status == 200:
                    data = await resp.json()
                    conversation_id = data.get('conversation_id')
                    summary.successful_requests += 1
                    log_func(f"[{user.email}] Conv created in {conv_duration:.0f}ms")
                else:
                    summary.failed_requests += 1
                    log_func(f"[{user.email}] Failed to create conv in {conv_duration:.0f}ms: {resp.status}", level="ERROR")
                    continue

            # 2. Upload PDF
            file_size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2)
            summary.total_file_size_mb += file_size_mb
            ingest_url = f"{config.base_url}/api/rag/ingest"
            form_data = aiohttp.FormData()
            form_data.add_field('file', open(file_path, 'rb'), filename=doc.filename, content_type='application/pdf')
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
                        summary.successful_requests += 1
                        task_id = data.get('task_id')
                        thread_id = data.get('thread_id')
                        
                        if not task_id:
                            # Synchronous processing
                            summary.ingestion_iterations += 1
                            summary.total_ingestion_time += upload_duration / 1000
                            processing_time = upload_duration / 1000
                            log_func(f"[{user.email}] Upload & Processing (Sync) for {doc.filename} in {upload_duration:.0f}ms - Total Ingest Time: {upload_duration/1000:.1f}s ({file_size_mb}MB)")
                        else:
                            log_func(f"[{user.email}] Upload success for {doc.filename} in {upload_duration:.0f}ms")
                    else:
                        summary.failed_requests += 1
                        log_func(f"[{user.email}] Upload logic fail for {doc.filename} in {upload_duration:.0f}ms: {data.get('error')}", level="ERROR")
                        continue
                else:
                    summary.failed_requests += 1
                    log_func(f"[{user.email}] Upload HTTP error for {doc.filename} in {upload_duration:.0f}ms: {resp.status}", level="ERROR")
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
                    if summary.stop_requested:
                        log_func(f"[{user.email}] Benchmark poll stopped by user")
                        return
                    retry_count += 1
                    async with session.get(poll_url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            status = data.get('status')
                            
                            if status == 'success':
                                thread_id = data.get('thread_id')
                                processing_time = data.get('processing_time_seconds', 0)
                                processing_times.append(processing_time)
                                summary.ingestion_iterations += 1
                                total_ingest_time = time.time() - poll_start
                                summary.total_ingestion_time += total_ingest_time
                                summary.successful_requests += 1
                                log_func(f"[{user.email}] Ingest complete for {doc.filename} in {total_ingest_time:.1f}s - Total Ingest Time: {total_ingest_time:.1f}s ({file_size_mb}MB)")
                                
                                # Add extracted text artifact metadata
                                summary.artifacts.append({
                                    "user_email": user.email,
                                    "type": "extracted_text",
                                    "thread_id": thread_id,
                                    "doc_name": doc.filename,
                                    "size_mb": file_size_mb
                                })
                                break
                            elif status in ['failure', 'revoked']:
                                log_func(f"[{user.email}] Ingest failed in {time.time() - poll_start:.1f}s: {status}", level="ERROR")
                                break
                        else:
                            log_func(f"[{user.email}] Poll error {resp.status}", level="WARNING")
                    await asyncio.sleep(2) # Prevent busy loop
                
                if not thread_id:
                    log_func(f"[{user.email}] Ingest timed out for {doc.filename}", level="ERROR")
                    continue
            elif thread_id:
                log_func(f"[{user.email}] Processing (Sync) completed during upload.")
                # Add extracted text artifact metadata for sync path
                summary.artifacts.append({
                    "user_email": user.email,
                    "type": "extracted_text",
                    "thread_id": thread_id,
                    "doc_name": doc.filename,
                    "size_mb": file_size_mb
                })

            # 4. Standard Question (Iterative if messages provided)
            msg_list = messages if messages else ["Summarize the key points of this document in 3 bullet points."]
            ai_response = ""
            
            # Simple keyword list for benchmark scoring (can be expanded)
            target_keywords = ["summary", "key", "important", "conclusion", "details", "chapter", "section"]
            keyword_hits = 0
            chat_transcript = []
            
            for idx, question in enumerate(msg_list):
                if summary.stop_requested:
                    log_func(f"[{user.email}] Benchmark chat stopped by user")
                    break
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
                    if resp.status == 429:
                        summary.rate_limit_hits += 1
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('success'):
                            ans_text = data.get('answer', '') or data.get('message', '')
                            ai_response = ans_text
                            summary.successful_requests += 1
                            summary.messages_sent += 1
                            summary.latency_trend.append(chat_duration)
                            log_func(f"[{user.email}] Response {idx+1} received in {chat_duration:.1f}s")
                            
                            # Add to transcript
                            chat_transcript.append({"role": "user", "content": question})
                            chat_transcript.append({"role": "bot", "content": ans_text, "latency": chat_duration})

                            # Simple keyword scoring
                            hits = sum(1 for word in target_keywords if word.lower() in ai_response.lower())
                            keyword_hits += hits
                            log_func(f"[{user.email}] Keyword analysis: {hits} hits found in response {idx+1}")
                        else:
                            summary.failed_requests += 1
                            log_func(f"[{user.email}] Chat {idx+1} logic fail in {chat_duration:.1f}s: {data.get('error')}", level="ERROR")
                    else:
                        summary.failed_requests += 1
                        log_func(f"[{user.email}] Chat {idx+1} HTTP error in {chat_duration:.1f}s: {resp.status}", level="ERROR")

            # Store result
            result_entry = {
                "filename": doc.filename,
                "processing_time": processing_time,
                "response_length": len(ai_response),
                "keyword_hits": keyword_hits,
                "response": ai_response,
                "status": "PASS" if ai_response and keyword_hits > 0 else "FAIL"
            }
            benchmark_results.append(result_entry)
            summary.keyword_hits += keyword_hits
            
            # Add chat transcript artifact metadata
            summary.artifacts.append({
                "user_email": user.email,
                "type": "chat_transcript",
                "conversation_id": conversation_id,
                "doc_name": doc.filename,
                "keyword_hits": keyword_hits,
                "transcript": chat_transcript
            })

        # Calculate consistency stats
        if processing_times:
            stdev_time = statistics.stdev(processing_times) if len(processing_times) > 1 else 0
            summary.consistency_stdev = stdev_time
            log_func(f"Ingestion Consistency (Stdev): {stdev_time:.2f}s across {len(processing_times)} files")
            
            # Phase 13: Green summary
            log_func(f"Total File Processing Time across {len(processing_times)} documents: {sum(processing_times):.2f}s (Complete)")

        # Store benchmark results in logs
        total_bench_duration = time.time() - scenario_start
        log_func(f"Quality Benchmark Complete. Total Duration: {total_bench_duration:.1f}s. Avg Keywords: {sum(r['keyword_hits'] for r in benchmark_results)/len(benchmark_results) if benchmark_results else 0}", details={"benchmark_data": benchmark_results})

    except Exception as e:
        total_bench_duration = time.time() - scenario_start
        log_func(f"[{user.email}] Benchmark exception after {total_bench_duration:.1f}s: {str(e)}", level="ERROR")
        summary.errors.append({"user": user.email, "error": str(e)})
