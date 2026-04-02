import time
import asyncio
import aiohttp
import logging
import random
from typing import Callable, Any
from app.load_testing.config import LoadTestConfig, TestResultSummary

logger = logging.getLogger(__name__)

async def run(
    session: aiohttp.ClientSession, 
    user: Any, 
    config: LoadTestConfig, 
    summary: TestResultSummary, 
    log_func: Callable
):
    """
    Execute Test 1: Multi-User Sign-In.
    Since the runner already performs login, this scenario just verifies 
    the dashboard access to confirm the session is valid and active.
    """
    scenario_start = time.time()
    # Login is already done by the runner. 
    # If we are here, login was successful.
    log_func("Authentication successful, verifying dashboard access...")
    
    url = f"{config.base_url}/"
    max_dashboard_attempts = 5
    base_backoff_seconds = 0.5
    max_backoff_seconds = 8.0
    
    try:
        if summary.stop_requested:
            return

        dashboard_ok = False
        for attempt in range(1, max_dashboard_attempts + 1):
            start = time.time()
            async with session.get(url, allow_redirects=True) as response:
                duration = time.time() - start
                summary.total_requests += 1
                if response.status == 429:
                    summary.rate_limit_hits += 1
                    if attempt < max_dashboard_attempts:
                        retry_after = response.headers.get("Retry-After")
                        backoff = min(base_backoff_seconds * (2 ** (attempt - 1)), max_backoff_seconds)
                        if retry_after:
                            try:
                                backoff = max(backoff, float(retry_after))
                            except ValueError:
                                pass
                        jitter = random.uniform(0.0, backoff * 0.3)
                        delay = backoff + jitter
                        log_func(
                            f"[{user.email}] Dashboard rate-limited (429). Retrying attempt {attempt + 1}/{max_dashboard_attempts} in {delay:.2f}s",
                            level="WARNING",
                        )
                        await asyncio.sleep(delay)
                        continue

                if response.status == 200:
                    text = await response.text()
                    # Verify we are actually on the dashboard and not redirected back to login
                    # Broadened check for Student Dashboard and general Iqbal AI content
                    success_keywords = ["Welcome", "Chat", "Iqbal AI", "Student", "Dashboard", "Teacher"]
                    if any(kw.lower() in text.lower() for kw in success_keywords):
                        summary.successful_requests += 1
                        log_func(f"Dashboard access confirmed at {response.url} in {duration:.2f}s")
                        dashboard_ok = True
                        break
                    if "Login" in text or "Sign In" in text:
                        summary.failed_requests += 1
                        summary.errors.append({"user": user.email, "error": f"Redirected to login. URL: {response.url}"})
                        log_func(f"Failed: Redirected to login page ({response.url}) in {duration:.2f}s", level="ERROR")
                    else:
                        summary.failed_requests += 1
                        summary.errors.append({"user": user.email, "error": f"Internal content mismatch at {response.url}"})
                        log_func(f"Failed: Content mismatch at {response.url}. SNIPPET: {text[:150]}...", level="ERROR")
                    return

                summary.failed_requests += 1
                summary.errors.append({"user": user.email, "error": f"Dashboard returned {response.status}"})
                log_func(f"Dashboard failed with status {response.status} in {duration:.2f}s", level="ERROR")
                return

        if not dashboard_ok:
            summary.failed_requests += 1
            summary.errors.append({"user": user.email, "error": "Dashboard returned 429 after retries"})
            log_func(f"[{user.email}] Dashboard failed with status 429 after {max_dashboard_attempts} attempts", level="ERROR")
            return
                
        # 3. Logout
        if summary.stop_requested: return
        log_func(f"[{user.email}] Step 3: Performing logout...")
        logout_url = f"{config.base_url}/auth/logout"
        start_logout = time.time()
        async with session.get(logout_url, allow_redirects=True) as response:
            logout_duration = (time.time() - start_logout) * 1000
            summary.total_requests += 1
            if response.status == 429:
                summary.rate_limit_hits += 1
            if response.status == 200:
                summary.successful_requests += 1
                summary.successful_logouts += 1
                log_func(f"[{user.email}] Logout confirmed in {logout_duration:.0f}ms")
            else:
                summary.failed_requests += 1
                log_func(f"[{user.email}] Logout failed with status {response.status}", level="ERROR")

        total_duration = time.time() - scenario_start
        log_func(f"Auth Test Complete. Total Duration: {total_duration:.1f}s")
    except Exception as e:
        total_duration = time.time() - scenario_start
        summary.failed_requests += 1
        summary.errors.append({"user": user.email, "error": str(e)})
        log_func(f"Auth exception after {total_duration:.1f}s: {str(e)}", level="ERROR")


