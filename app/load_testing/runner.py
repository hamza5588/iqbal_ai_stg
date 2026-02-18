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
from app.utils.db import get_session_factory

# Configure logging
logger = logging.getLogger(__name__)

class LoadTestRunner:
    """Core execution engine for load tests"""
    
    def __init__(self, config: LoadTestConfig, result_id: int):
        self.config = config
        self.result_id = result_id
        self.summary = TestResultSummary()
        self.is_running = False
        self._session_factory = get_session_factory()

    async def run(self):
        """Main entry point to run the load test"""
        self.is_running = True
        logger.info(f"Starting load test {self.result_id}: {self.config.test_type.value} with {self.config.concurrent_users} users")
        
        # Update status to RUNNING
        self._update_status(LoadTestStatus.RUNNING)
        self._log(f"Test started. Configuration: {self.config}")

        try:
            # Get test users
            users = self._get_test_users()
            if not users:
                raise ValueError(f"No users found for test set {self.config.test_user_set_id}")
            
            # Limit users to concurrent_users count if we have more users than needed
            # Or cycle through them if we have fewer (though usually we matched them)
            if len(users) > self.config.concurrent_users:
                users = users[:self.config.concurrent_users]
            
            logger.info(f"Using {len(users)} users for the test")

            # Create async tasks for each user/worker
            tasks = []
            start_time = time.time()
            
            # Use a semaphore to control concurrency if needed, though we span workers per user
            # For now, we spawn one worker per user up to concurrent_users
            
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
                # We might need separate sessions for each user to simulate real browsers properly
                # actually, aiohttp ClientSession shares cookies if reused. 
                # So we MUST create a separate session for each worker/user to isolate cookies.
                pass

            # Launch workers
            # We use a list of worker coroutines
            for i, user in enumerate(users):
                tasks.append(self._worker(i, user, start_time))
            
            # Wait for all tasks to complete
            await asyncio.gather(*tasks)
            
            # Test run complete
            self._update_status(LoadTestStatus.COMPLETED)
            self._log("Test run completed successfully")
            
        except Exception as e:
            logger.error(f"Test run failed: {str(e)}")
            self._log(f"Test run failed: {str(e)}", level="ERROR", details={"traceback": traceback.format_exc()})
            self._update_status(LoadTestStatus.FAILED)
        finally:
            self.is_running = False
            # Save final metrics
            self._save_metrics()

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
            self.summary.errors.append({"user": user_email, "error": str(e), "traceback": traceback.format_exc()})
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
            await test_teacher_flow.run(session, user, config, self.summary, self._log)
            
        elif test_type == TestType.STUDENT_CHAT_CONCURRENT:
            from app.load_testing.scenarios import test_student_chat
            await test_student_chat.run(session, user, config, self.summary, self._log)
            
        elif test_type == TestType.TEACHER_RAG_SEQUENTIAL:
            from app.load_testing.scenarios import test_teacher_sequential
            await test_teacher_sequential.run(session, user, config, self.summary, self._log)
            
        elif test_type == TestType.STUDENT_LESSON_SEQUENTIAL:
            from app.load_testing.scenarios import test_student_sequential
            await test_student_sequential.run(session, user, config, self.summary, self._log)
            
        elif test_type == TestType.DOC_UPLOAD_REPEAT:
            from app.load_testing.scenarios import test_teacher_repeat_ingest
            await test_teacher_repeat_ingest.run(session, user, config, self.summary, self._log)
            
        elif test_type == TestType.RAG_QUALITY_BENCHMARK:
            from app.load_testing.scenarios import test_rag_pipeline_quality
            await test_rag_pipeline_quality.run(session, user, config, self.summary, self._log)
            
        else:
            logger.error(f"Unknown test type: {test_type}")
            self._log(f"Unknown test type: {test_type}", level="ERROR")

    def _get_test_users(self) -> List[TestUser]:
        """Retrieve test users from DB using a separate sync session"""
        session = self._session_factory()
        try:
            if self.config.test_user_set_id:
                users = session.query(TestUser).filter_by(
                    user_set_id=self.config.test_user_set_id, 
                    is_active=True
                ).all()
                # Detach objects from session so they can be used after session closes
                session.expunge_all()
                self._log(f"Successfully loaded {len(users)} users from Set ID {self.config.test_user_set_id}")
                return users
            return []
        finally:
            session.close()

    def _update_status(self, status: LoadTestStatus):
        """Update test status in DB"""
        session = self._session_factory()
        try:
            result = session.query(LoadTestResult).get(self.result_id)
            if result:
                result.status = status.value
                if status == LoadTestStatus.COMPLETED or status == LoadTestStatus.FAILED:
                    result.completed_at = datetime.utcnow()
                session.commit()
        except Exception as e:
            logger.error(f"Failed to update status: {str(e)}")
        finally:
            session.close()

    def _log(self, message: str, level: str = "INFO", details: Optional[Dict] = None):
        """Write a log entry to DB"""
        # Also log to file/console
        if level == "ERROR":
            logger.error(f"[Test {self.result_id}] {message}")
        else:
            logger.info(f"[Test {self.result_id}] {message}")
            
        session = self._session_factory()
        try:
            log_entry = LoadTestLog(
                result_id=self.result_id,
                level=level,
                message=message,
                details=details,
                timestamp=datetime.utcnow()
            )
            session.add(log_entry)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to write log to DB: {str(e)}")
        finally:
            session.close()

    def _save_metrics(self):
        """Save final metrics to DB"""
        session = self._session_factory()
        try:
            result = session.query(LoadTestResult).get(self.result_id)
            if result:
                metrics = {
                    "total_requests": self.summary.total_requests,
                    "successful_requests": self.summary.successful_requests,
                    "failed_requests": self.summary.failed_requests,
                    "errors": self.summary.errors
                }
                result.metrics = metrics
                session.commit()
        except Exception as e:
            logger.error(f"Failed to save metrics: {str(e)}")
        finally:
            session.close()
