import asyncio
import aiohttp
import logging
import time
import json
import traceback
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.load_testing.config import LoadTestConfig, TestType, TestResultSummary
from app.load_testing.models import TestUser, LoadTestResult, LoadTestLog, LoadTestStatus
from app.utils.db import get_db, close_db

# Configure logging
logger = logging.getLogger(__name__)

class LoadTestRunner:
    """Core execution engine for load tests"""
    
    def __init__(self, app, config: LoadTestConfig, result_id: int):
        self.app = app
        self.config = config
        self.result_id = result_id
        self.summary = TestResultSummary()
        self.is_running = False
        self.stop_requested = False

    def stop(self):
        """Signal the runner to stop"""
        self.stop_requested = True
        self._log("Stop signal received. Terminating workers...", level="WARNING")

    async def run(self):
        """Main entry point to run the load test"""
        with self.app.app_context():
            self.is_running = True
            logger.info(f"Starting load test {self.result_id}: {self.config.test_type.value} with {self.config.concurrent_users} users")
            
            # Update status to RUNNING
            self._update_status(LoadTestStatus.RUNNING)
            self._log(f"Test started. Configuration: {self.config}")
            
            # Load messages upfront (safety First)
            self.messages = []
            if self.config.csv_file_id:
                try:
                    from app.load_testing.message_csv_manager import MessageCSVManager
                    self.messages = MessageCSVManager.get_messages(self.config.csv_file_id)
                    self._log(f"Loaded {len(self.messages)} messages from CSV ID {self.config.csv_file_id}")
                except Exception as e:
                    logger.error(f"Failed to load messages from CSV {self.config.csv_file_id}: {e}")
                    self._log(f"Failed to load messages from CSV: {str(e)}", level="ERROR")

            try:
                # Get test users
                users = self._get_test_users()
                if not users:
                    raise ValueError(f"No users found for test set {self.config.test_user_set_id}")
                
                # Limit users to concurrent_users count
                if len(users) > self.config.concurrent_users:
                    users = users[:self.config.concurrent_users]
                
                logger.info(f"Using {len(users)} users for the test")

                start_time = time.time()
                tasks = []
                for i, user in enumerate(users):
                    tasks.append(self._worker(i, user, start_time))
                
                await asyncio.gather(*tasks)
                
                total_duration = time.time() - start_time
                
                if self.stop_requested:
                    self._update_status(LoadTestStatus.FAILED) # Or we could add STOPPED, but FAILED is safer for now
                    self._log(f"Test stopped by user after {total_duration:.1f}s", level="WARNING")
                elif self.summary.failed_requests > 0:
                    self._update_status(LoadTestStatus.FAILED)
                    self._log(f"Test completed with {self.summary.failed_requests} failures in {total_duration:.1f}s", level="ERROR")
                else:
                    self._update_status(LoadTestStatus.COMPLETED)
                    self._log(f"Test run completed successfully in {total_duration:.1f}s")
                
            except Exception as e:
                total_duration = time.time() - (start_time if 'start_time' in locals() else time.time())
                logger.error(f"Test run failed after {total_duration:.1f}s: {str(e)}")
                self._log(f"Test run failed: {str(e)}", level="ERROR")
                self._update_status(LoadTestStatus.FAILED)
            finally:
                self.is_running = False
                self._save_metrics()
                # Important: cleanup session at end of thread
                close_db()

    async def _worker(self, worker_id: int, user: TestUser, start_time: float):
        """Async worker for a single user"""
        user_email = user.email
        logger.info(f"Worker {worker_id} started for user {user_email}")
        
        try:
            # Create a dedicated session for this user to isolate cookies
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
                
                # 1. Login
                login_success = await self._login(session, user)
                if not login_success:
                    self.summary.failed_requests += 1
                    self.summary.errors.append({"user": user_email, "error": "Login failed"})
                    return

                # 2. Execute Scenario based on test type
                # We will import scenarios dynamically or use a dispatcher
                await self._dispatch_scenario(session, user, self.config)
                
        except Exception as e:
            logger.error(f"Worker {worker_id} execution error: {str(e)}")
            self.summary.errors.append({"user": user_email, "error": str(e)})
            self._log(f"Worker {worker_id} error: {str(e)}", level="ERROR")

    async def _login(self, session: aiohttp.ClientSession, user: TestUser) -> bool:
        """Perform login flow"""
        url = f"{self.config.base_url}/auth/login"
        payload = {
            "useremail": user.email,
            "password": user.password
        }
        
        start = time.time()
        try:
            async with session.post(url, data=payload, allow_redirects=False) as response:
                duration = time.time() - start
                
                # Flask login typically redirects on success (302)
                if response.status == 302:
                    # Verify we got a session cookie
                    cookies = session.cookie_jar.filter_cookies(self.config.base_url)
                    if 'session' in cookies:
                        logger.info(f"User {user.email} logged in successfully ({duration:.2f}s)")
                        return True
                    else:
                        logger.warning(f"User {user.email} login redirect but no session cookie found for {self.config.base_url}")
                        self._log(f"User {user.email} no session cookie", level="WARNING")
                        return False
                elif response.status == 200:
                    text = await response.text()
                    try:
                        data = json.loads(text)
                        if data.get("success") is True:
                            # Verify we got a session cookie
                            cookies = session.cookie_jar.filter_cookies(self.config.base_url)
                            if 'session' in cookies:
                                logger.info(f"User {user.email} logged in successfully via JSON ({duration:.2f}s)")
                                return True
                            else:
                                logger.warning(f"User {user.email} login success JSON but no session cookie")
                                self._log(f"User {user.email} no session cookie", level="WARNING")
                                return False
                        else:
                            logger.warning(f"User {user.email} login failed via JSON: {data.get('error')}")
                            self._log(f"User {user.email} login failed: {data.get('error')}", level="ERROR")
                            return False
                    except json.JSONDecodeError:
                        if "Invalid credentials" in text:
                            logger.warning(f"User {user.email} invalid credentials")
                            self._log(f"User {user.email} invalid credentials", level="ERROR")
                        else:
                            logger.warning(f"User {user.email} login returned 200 but not redirected and not JSON success. Text: {text[:100]}...")
                            self._log(f"User {user.email} login returned 200 (unexpected)", level="WARNING")
                        return False
                else:
                    text = await response.text()
                    logger.warning(f"User {user.email} login failed with status {response.status}. Text: {text[:200]}...")
                    self._log(f"User {user.email} login failed with status {response.status}", level="ERROR")
                    return False
        except Exception as e:
            logger.error(f"User {user.email} login exception: {str(e)}")
            self._log(f"User {user.email} login exception: {str(e)}", level="ERROR")
            return False

    async def _dispatch_scenario(self, session: aiohttp.ClientSession, user: TestUser, config: LoadTestConfig):
        """Dispatch execution to the appropriate scenario function"""
        test_type = config.test_type
        
        # Determine the target function based on TestType
        if test_type == TestType.MULTI_USER_SIGN_IN:
            from app.load_testing.scenarios import test_auth
            await test_auth.run(session, user, config, self.summary, self._log)
            
        elif test_type == TestType.TEACHER_FLOW_CONCURRENT:
            from app.load_testing.scenarios import test_teacher_flow
            await test_teacher_flow.run(session, user, config, self.summary, self._log, messages=self.messages)
            
        elif test_type == TestType.STUDENT_CHAT_CONCURRENT:
            from app.load_testing.scenarios import test_student_chat
            await test_student_chat.run(session, user, config, self.summary, self._log, messages=self.messages)
            
        elif test_type == TestType.TEACHER_RAG_SEQUENTIAL:
            from app.load_testing.scenarios import test_teacher_sequential
            await test_teacher_sequential.run(session, user, config, self.summary, self._log, messages=self.messages)
            
        elif test_type == TestType.STUDENT_LESSON_SEQUENTIAL:
            from app.load_testing.scenarios import test_student_sequential
            await test_student_sequential.run(session, user, config, self.summary, self._log, messages=self.messages)
            
        elif test_type == TestType.DOC_UPLOAD_REPEAT:
            from app.load_testing.scenarios import test_teacher_repeat_ingest
            await test_teacher_repeat_ingest.run(session, user, config, self.summary, self._log, messages=self.messages)
            
        elif test_type == TestType.RAG_QUALITY_BENCHMARK:
            from app.load_testing.scenarios import test_rag_pipeline_quality
            await test_rag_pipeline_quality.run(session, user, config, self.summary, self._log, messages=self.messages)
            
        else:
            logger.error(f"Unknown test type: {test_type}")
            self._log(f"Unknown test type: {test_type}", level="ERROR")

    def _get_test_users(self) -> List[TestUser]:
        """Retrieve test users from DB, joining with main User table to get live passwords"""
        from app.models.database_models import User
        db = get_db()
        if self.config.test_user_set_id:
            results = db.query(TestUser, User.password.label('live_password')).join(
                User, TestUser.real_user_id == User.id
            ).filter(
                TestUser.user_set_id == self.config.test_user_set_id,
                TestUser.is_active == True
            ).all()
            
            users = []
            for test_user, live_password in results:
                test_user.password = live_password
                users.append(test_user)
            
            self._log(f"Successfully loaded {len(users)} users from Set ID {self.config.test_user_set_id} with live passwords")
            return users
        return []

    def _update_status(self, status: LoadTestStatus):
        """Update test status in DB"""
        db = get_db()
        try:
            result = db.get(LoadTestResult, self.result_id)
            if result:
                result.status = status.value
                if status == LoadTestStatus.COMPLETED or status == LoadTestStatus.FAILED:
                    result.completed_at = datetime.utcnow()
                db.commit()
        except Exception as e:
            logger.error(f"Failed to update status: {str(e)}")

    def _log(self, message: str, level: str = "INFO", details: Optional[Dict] = None):
        """Write a log entry to DB"""
        if level == "ERROR":
            logger.error(f"[Test {self.result_id}] {message}")
        else:
            logger.info(f"[Test {self.result_id}] {message}")
            
        db = get_db()
        try:
            log_entry = LoadTestLog(
                result_id=self.result_id,
                level=level,
                message=message,
                details=details,
                timestamp=datetime.utcnow()
            )
            db.add(log_entry)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to write log to DB: {str(e)}")

    def _save_metrics(self):
        """Save final metrics to DB"""
        db = get_db()
        try:
            result = db.get(LoadTestResult, self.result_id)
            if result:
                metrics = {
                    "total_requests": self.summary.total_requests,
                    "successful_requests": self.summary.successful_requests,
                    "failed_requests": self.summary.failed_requests,
                    "messages_sent": self.summary.messages_sent,
                    "total_file_size_mb": round(self.summary.total_file_size_mb, 2),
                    "total_ingestion_time": round(self.summary.total_ingestion_time, 2),
                    "successful_logouts": self.summary.successful_logouts,
                    "keyword_hits": self.summary.keyword_hits,
                    "consistency_stdev": self.summary.consistency_stdev,
                    "latency_trend": self.summary.latency_trend,
                    "lesson_saved": self.summary.lesson_saved,
                    "errors": self.summary.errors
                }
                result.metrics = metrics
                db.commit()
        except Exception as e:
            logger.error(f"Failed to save metrics: {str(e)}")
