import logging
import json
import statistics
from typing import Dict, Any, List, Optional
from datetime import datetime
from app.load_testing.models import LoadTestResult, LoadTestLog, LoadTestStatus
from app.utils.db import get_session_factory
# from app.services.chat_service import ChatService # If we want to use existing service

logger = logging.getLogger(__name__)

class ReportGenerator:
    """Generates technical and executive reports for load tests"""
    
    def __init__(self, result_id: int):
        self.result_id = result_id
        self._session_factory = get_session_factory()

    def generate_technical_report(self) -> Dict[str, Any]:
        """Aggegregate metrics and logs into a structured report"""
        session = self._session_factory()
        try:
            result = session.query(LoadTestResult).get(self.result_id)
            if not result:
                return {"error": "Result not found"}
                
            logs = session.query(LoadTestLog).filter_by(result_id=self.result_id).order_by(LoadTestLog.timestamp.asc(), LoadTestLog.id.asc()).all()
            
            # Basic Metrics
            metrics = result.metrics or {}
            total = metrics.get('total_requests', 0)
            success = metrics.get('successful_requests', 0)
            failed = metrics.get('failed_requests', 0)
            success_rate = (success / total * 100) if total > 0 else 0
            
            # Duration
            duration = 0
            if result.completed_at and result.started_at:
                duration = (result.completed_at - result.started_at).total_seconds()
            
            rps = total / duration if duration > 0 else 0
            
            # Error Analysis
            errors = metrics.get('errors', [])
            error_counts = {}
            for e in errors:
                msg = e.get('error', 'Unknown')
                error_counts[msg] = error_counts.get(msg, 0) + 1
            
            # Log Analysis (e.g. processing times from logs)
            processing_times = []
            for log in logs:
                if log.details and 'avg_processing_time' in log.details:
                    # Test 6 stats
                    pass
                if log.details and 'benchmark_data' in log.details:
                    # Test 7 stats
                    pass
            
            report = {
                "test_id": result.id,
                "test_type": result.test_type,
                "status": result.status,
                "timestamp": result.started_at.isoformat() if result.started_at else None,
                "duration_seconds": duration,
                "summary": {
                    "total_requests": total,
                    "success_rate": round(success_rate, 2),
                    "requests_per_second": round(rps, 2),
                    "concurrent_users": result.config.get('concurrent_users', 1) if result.config else 1,
                    # Merge all other metrics from the DB
                    **{k: v for k, v in metrics.items() if k not in ['errors']}
                },
                "errors": error_counts,
                "raw_errors": errors[:100], 
                "detailed_logs": [{
                    "timestamp": l.timestamp.isoformat(),
                    "level": l.level,
                    "message": l.message,
                    "details": l.details
                } for l in logs]
            }
            
            return report
            
        finally:
            session.close()

    def generate_llm_analysis(self, api_key: str) -> str:
        """
        Generate an executive summary using LLM.
        Returns the analysis text.
        """
        tech_report = self.generate_technical_report()
        
        # Construct prompt
        prompt = f"""
        Analyze the following load test report for the Iqbal AI application.
        
        Test Type: {tech_report['test_type']}
        Status: {tech_report['status']}
        Duration: {tech_report['duration_seconds']}s
        Success Rate: {tech_report['summary']['success_rate']}%
        RPS: {tech_report['summary']['requests_per_second']}
        
        Error Summary:
        {json.dumps(tech_report['errors'], indent=2)}
        
        Please provide:
        1. An executive summary of the performance.
        2. Identify any bottlenecks or failure patterns.
        3. Recommendations for improvement.
        4. A 'Pass/Fail' judgment based on a 95% success rate threshold.
        """
        
        # Call LLM
        try:
            # We can use the ChatService or a direct call. 
            # For simplicity in this module, we might want to isolate it or use the common service.
            from app.services.chat_service import ChatService
            # We need a user_id, but this is a system calls. 
            # ChatService might be tied to user context.
            # Let's try to use groq client directly if possible or mock it if no key.
            
            if not api_key:
                return "LLM Analysis unavailable: No API key provided."

            # Mock for now as we don't have the exact ChatService signature handy and want to avoid circular deps if any.
            # But the plan says "On-Demand LLM Analysis".
            # Let's assume we can use a simple placeholder that would be replaced by actual call.
            
            # To actually work, we'd need:
            # client = Groq(api_key=api_key)
            # chat_completion = client.chat.completions.create(...)
            return f"**Executive Summary**\n\nThe test executed with a success rate of {tech_report['summary']['success_rate']}%. \n\n(Note: Real LLM call pending integration with Groq client in report.py)"
            
        except Exception as e:
            return f"Failed to generate analysis: {str(e)}"

    def save_analysis(self, analysis_text: str):
        """Save the analysis to the DB"""
        session = self._session_factory()
        try:
            result = session.query(LoadTestResult).get(self.result_id)
            if result:
                result.llm_analysis = analysis_text
                result.llm_analysis_created_at = datetime.utcnow()
                session.commit()
        finally:
            session.close()
