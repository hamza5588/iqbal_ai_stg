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
        
        if "error" in tech_report:
            return f"Cannot generate analysis: {tech_report['error']}"

        # Construct a detailed prompt for the LLM
        # We pass the full JSON as requested by the user
        report_json = json.dumps(tech_report, indent=2)
        
        system_prompt = """
        You are a Senior Performance Engineer and AI Analyst at Iqbal AI. 
        Your task is to analyze raw load test data and provide a premium executive summary.
        
        Guidelines:
        1. BE PROFESSIONAL: Use formal, concise, and technical language.
        2. BE DATA-DRIVEN: Reference specific metrics (Success Rate, RPS, Latency, Stdev).
        3. IDENTIFY PATTERNS: Look for bottlenecks, degradation over time, or concurrency issues.
        4. PASS/FAIL: Strictly judge the test based on a 95% success rate threshold unless specified otherwise.
        5. FORMATTING: Use Markdown for better readability (headers, bold text, bullet points).
        
        The report you receive is a JSON object containing metrics and logs.
        """
        
        user_prompt = f"""
        Please analyze the following Load Test Report JSON and provide an executive summary:
        
        {report_json}
        
        Structure your response as follows:
        # Executive Summary
        (A high-level summary of the test purpose and overall result)
        
        ## Performance KPIs
        (Bullet points highlighting key performance indicators like avg latency, max RPS, and success rate)
        
        ## Bottlenecks & Anomalies
        (Detailed analysis of any issues found in the logs or metrics)
        
        ## Recommendations
        (Clear, actionable steps for the engineering team)
        
        ## Final Verdict: [PASS/FAIL]
        """
        
        try:
            from app.models import ChatModel
            
            if not api_key:
                return "LLM Analysis unavailable: No API key provided. Please configure it in the Admin Dashboard."

            # Instantiate ChatModel (system call, user_id=None)
            chat_model = ChatModel(api_key=api_key, user_id=None)
            
            # Generate response
            analysis = chat_model.generate_response(
                input_text=user_prompt,
                system_prompt=system_prompt
            )
            
            return analysis
            
        except Exception as e:
            logger.error(f"Failed to generate LLM analysis: {str(e)}")
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
