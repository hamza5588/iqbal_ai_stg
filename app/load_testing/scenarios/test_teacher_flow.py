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
    Execute Test 2: Multi-Teacher Document Upload Flow (Concurrent).
    Flow:
    1. Create Conversation
    2. Upload PDF (ingest)
    3. Poll status
    4. Chat with PDF
    5. Finalize Lesson
    6. Create Lesson
    """
    scenario_start = time.time()
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
        log_func(f"[{user.email}] Step 1: Creating conversation...")
        start_time = time.time()
        
        create_conv_url = f"{config.base_url}/create_conversation"
        conversation_id = None
        
        async with session.post(create_conv_url, json={"title": "Load Test Chat"}, headers={"Content-Type": "application/json"}) as resp:
            duration = (time.time() - start_time) * 1000
            summary.total_requests += 1 # Action 1: Create Conversation
            if resp.status == 429:
                summary.rate_limit_hits += 1
            if resp.status == 200:
                data = await resp.json()
                conversation_id = data.get('conversation_id')
                log_func(f"[{user.email}] Conv created: {conversation_id} in {duration:.0f}ms")
                summary.successful_requests += 1
            else:
                summary.failed_requests += 1
                log_func(f"[{user.email}] Failed to create conv: {resp.status} in {duration:.0f}ms", level="ERROR")
                return

        # 2. Upload PDF
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
                    thread_id = data.get('thread_id') 
                    file_size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2)
                    summary.total_file_size_mb += file_size_mb
                    summary.successful_requests += 1
                    
                    if not task_id:
                        # Synchronous processing
                        summary.ingestion_iterations += 1
                        summary.total_ingestion_time += upload_duration / 1000
                        log_func(f"[{user.email}] Upload & Processing (Sync) success in {upload_duration:.0f}ms - Total Ingest Time: {upload_duration/1000:.1f}s ({file_size_mb}MB)")
                        # Phase 13: Green summary
                        log_func(f"[{user.email}] Total File Processing Time: {upload_duration/1000:.1f}s (Complete)")
                    else:
                        log_func(f"[{user.email}] Upload success in {upload_duration:.0f}ms. Task: {task_id}")
                else:
                    summary.failed_requests += 1
                    log_func(f"[{user.email}] Upload logic fail in {upload_duration:.0f}ms: {data.get('error')}", level="ERROR")
                    return
            else:
                body_text = ""
                body_json = {}
                try:
                    body_json = await resp.json()
                except Exception:
                    try:
                        body_text = await resp.text()
                    except Exception:
                        body_text = ""

                # Expected guardrail: oversized PDFs are intentionally rejected by API.
                # Treat as skipped user flow (not a platform failure) so load-test error
                # metrics focus on true regressions.
                error_code = (body_json or {}).get("code")
                error_text = (body_json or {}).get("error") or body_text
                if resp.status == 400 and (
                    error_code == "PDF_TOO_LARGE"
                    or "too large for ingestion" in (error_text or "").lower()
                ):
                    log_func(
                        f"[{user.email}] Upload skipped in {upload_duration:.0f}ms: oversized document ({filename})",
                        level="WARNING",
                    )
                    return

                summary.failed_requests += 1
                log_func(f"[{user.email}] Upload HTTP error in {upload_duration:.0f}ms: {resp.status}", level="ERROR")
                return

        # 3. Poll Status
        if task_id:
            log_func(f"[{user.email}] Step 3: Ingestion processing (Async)...")
            poll_url = f"{config.base_url}/api/rag/ingest/status/{task_id}"
            # Allow up to ~5 minutes for ingestion (150 * 2s)
            max_retries = 150
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
                            summary.ingestion_iterations += 1
                            total_ingest_time = time.time() - poll_start
                            summary.total_ingestion_time += total_ingest_time
                            log_func(f"[{user.email}] Ingest complete in {total_ingest_time:.1f}s (Processing time: {data.get('processing_time_seconds')}s) - Total Ingest Time: {total_ingest_time:.1f}s ({file_size_mb}MB)")
                            # Phase 13: Green summary
                            log_func(f"[{user.email}] Total File Processing Time: {total_ingest_time:.1f}s (Complete)")
                            
                            # Add extracted text artifact metadata
                            summary.artifacts.append({
                                "user_email": user.email,
                                "type": "extracted_text",
                                "thread_id": thread_id,
                                "doc_name": filename,
                                "size_mb": file_size_mb
                            })
                            break
                        elif status in ['failure', 'revoked']:
                            log_func(f"[{user.email}] Ingest failed in {time.time() - poll_start:.1f}s: {status}", level="ERROR")
                            return
                    else:
                        log_func(f"[{user.email}] Poll error {resp.status}", level="WARNING")
                
                await asyncio.sleep(2)
            
            if retry_count >= max_retries:
                log_func(f"[{user.email}] Ingest timed out in {time.time() - poll_start:.1f}s", level="ERROR")
                return
        else:
            log_func(f"[{user.email}] Step 3: Processing (Synchronous) completed during Step 2.")

        msg_list = messages if messages else ["Create a lesson plan based on this document."]
        log_func(f"[{user.email}] Step 4: Iterative Chat starting for {len(msg_list)} messages...")
        
        chat_transcript = []
        for idx, chat_msg in enumerate(msg_list):
            if summary.stop_requested:
                log_func(f"[{user.email}] Chat sequence stopped by user")
                break
            log_func(f"[{user.email}] Sending message {idx+1}/{len(msg_list)}: \"{chat_msg[:50]}...\"")
            chat_url = f"{config.base_url}/api/rag/chat"
            payload = {"message": chat_msg, "thread_id": thread_id, "conversation_id": conversation_id}
            
            chat_start = time.time()
            async with session.post(chat_url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                chat_duration = time.time() - chat_start
                summary.total_requests += 1 # Action: Chat Message
                if resp.status == 429:
                    summary.rate_limit_hits += 1
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        summary.messages_sent += 1
                        summary.successful_requests += 1
                        summary.latency_trend.append(chat_duration)
                        ans_text = data.get('answer') or data.get('message') or ""
                        ans_snippet = ans_text[:50].replace('\n', ' ')
                        log_func(f"[{user.email}] Response {idx+1} received in {chat_duration:.1f}s: \"{ans_snippet}...\"")
                        
                        # Add to transcript
                        chat_transcript.append({"role": "user", "content": chat_msg})
                        chat_transcript.append({"role": "bot", "content": ans_text, "latency": chat_duration})
                    else:
                        summary.failed_requests += 1
                        err = data.get('error') or data.get('message') or data.get('code') or "Unknown error"
                        log_func(
                            f"[{user.email}] Chat {idx+1} fail in {chat_duration:.1f}s: {err} (full_response={str(data)[:300]})",
                            level="ERROR",
                        )
                else:
                    summary.failed_requests += 1
                    # Read response body so we know why the 400/500 happened
                    body_text = ""
                    try:
                        body_text = await resp.text()
                    except Exception:
                        body_text = ""
                    # Keep logs short to avoid huge output
                    body_snippet = (body_text or "").strip().replace("\n", " ")
                    if len(body_snippet) > 400:
                        body_snippet = body_snippet[:400] + "..."
                    log_func(
                        f"[{user.email}] Chat {idx+1} HTTP error in {chat_duration:.1f}s: {resp.status} (body={body_snippet})",
                        level="ERROR",
                    )

        # 5. Get Finalized Lesson Status
        log_func(f"[{user.email}] Step 5: Finalizing lesson...")
        finalized_url = f"{config.base_url}/api/rag/thread/{thread_id}/finalized-lesson"
        lesson_title = "Generated Lesson"
        lesson_content = "Default content"
        
        fin_start = time.time()
        async with session.get(finalized_url) as resp:
            fin_duration = (time.time() - fin_start) * 1000
            if resp.status == 200:
                data = await resp.json()
                if data.get('success'):
                    lesson_title = data.get('lesson_title') or lesson_title
                    lesson_content = data.get('last_lesson_text') or lesson_content
                    log_func(f"[{user.email}] Lesson finalized in {fin_duration:.0f}ms")
            else:
                log_func(f"[{user.email}] Finalized check failed in {fin_duration:.0f}ms: {resp.status}", level="WARNING")

        # 6. Create Lesson (Finalize)
        log_func(f"[{user.email}] Step 6: Saving lesson to database...")
        create_lesson_url = f"{config.base_url}/api/lessons/create"
        lesson_payload = {
            "title": f"{lesson_title} - LoadTest {time.time()}",
            "content": lesson_content,
            "focus_area": "General",
            "grade_level": "General",
            "summary": "Generated during load test"
        }
        
        save_start = time.time()
        async with session.post(create_lesson_url, json=lesson_payload, headers={"Content-Type": "application/json"}) as resp:
            save_duration = (time.time() - save_start) * 1000
            summary.total_requests += 1 # Action 4: Save Lesson
            if resp.status == 200:
                data = await resp.json()
                if data.get('success'):
                    summary.successful_requests += 1
                    summary.lesson_saved = True
                    log_func(f"[{user.email}] Lesson saved in {save_duration:.0f}ms")
                    
                    # Add remaining artifact metadata (Chat & Lesson)
                    summary.artifacts.append({
                        "user_email": user.email,
                        "type": "chat_transcript",
                        "conversation_id": conversation_id,
                        "doc_name": filename,
                        "transcript": chat_transcript
                    })
                    summary.artifacts.append({
                        "user_email": user.email,
                        "type": "lesson_content",
                        "lesson_id": data.get('id'),
                        "doc_name": filename
                    })
                else:
                    summary.failed_requests += 1
                    log_func(f"[{user.email}] Save logic fail in {save_duration:.0f}ms: {data.get('error')}", level="ERROR")
            else:
                summary.failed_requests += 1
                log_func(f"[{user.email}] Save HTTP error in {save_duration:.0f}ms: {resp.status}", level="ERROR")

        total_user_duration = time.time() - scenario_start
        log_func(f"[{user.email}] Teacher Flow Complete. Total Duration: {total_user_duration:.1f}s")

    except Exception as e:
        total_user_duration = time.time() - scenario_start
        log_func(f"[{user.email}] Teacher flow exception after {total_user_duration:.1f}s: {str(e)}", level="ERROR")
        summary.errors.append({"user": user.email, "error": str(e)})
