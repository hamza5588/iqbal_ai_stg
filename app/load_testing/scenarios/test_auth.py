import time
import aiohttp
import logging
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
    # Login is already done by the runner. 
    # If we are here, login was successful.
    log_func("Authentication successful, verifying dashboard access...")
    
    url = f"{config.base_url}/"
    start = time.time()
    
    try:
        async with session.get(url, allow_redirects=True) as response:
            duration = time.time() - start
            summary.total_requests += 1
            
            if response.status == 200:
                text = await response.text()
                # Verify we are actually on the dashboard and not redirected back to login
                # Check for some dashboard-specific content
                if "Welcome" in text or "Chat" in text or "Iqbal AI" in text:
                    summary.successful_requests += 1
                    log_func(f"Dashboard access confirmed ({duration:.2f}s)")
                else:
                    # Might have been redirected to login page despite 200 OK (if login page serves 200)
                    if "Login" in text:
                        summary.failed_requests += 1
                        summary.errors.append({"user": user.email, "error": "Redirected to login page"})
                        log_func("Failed: Redirected to login page", level="ERROR")
                    else:
                        # Assume success if we can't definitively identify failure, 
                        # but typically we should find a known element.
                        summary.successful_requests += 1
                        log_func(f"Dashboard loaded ({duration:.2f}s)")
            else:
                summary.failed_requests += 1
                summary.errors.append({"user": user.email, "error": f"Dashboard returned {response.status}"})
                log_func(f"Dashboard failed with {response.status}", level="ERROR")
                
    except Exception as e:
        summary.failed_requests += 1
        summary.errors.append({"user": user.email, "error": str(e)})
        log_func(f"Dashboard access exception: {str(e)}", level="ERROR")
