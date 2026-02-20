from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any

class TestType(Enum):
    MULTI_USER_SIGN_IN = "multi_user_sign_in"
    TEACHER_FLOW_CONCURRENT = "teacher_flow_concurrent"
    STUDENT_CHAT_CONCURRENT = "student_chat_concurrent"
    TEACHER_RAG_SEQUENTIAL = "teacher_rag_sequential"
    STUDENT_LESSON_SEQUENTIAL = "student_lesson_sequential"
    DOC_UPLOAD_REPEAT = "doc_upload_repeat"
    RAG_QUALITY_BENCHMARK = "rag_quality_benchmark"

class TargetEnvironment(Enum):
    LOCALHOST = "http://localhost:5000"
    STAGING = "https://staging.iqbalai.com"
    PRODUCTION = "https://iqbalai.com"
    CUSTOM = "custom"

@dataclass
class LoadTestConfig:
    test_type: TestType
    target_env: TargetEnvironment
    custom_url: Optional[str] = None
    concurrent_users: int = 1
    duration_seconds: int = 60
    ramp_up_seconds: int = 0
    test_user_set_id: Optional[int] = None
    test_doc_set_id: Optional[int] = None
    csv_file_id: Optional[int] = None
    lesson_id: Optional[int] = None  # For student chat tests
    
    # Advanced options
    headless: bool = True  # For browser-based tests if implemented later
    stop_on_error: bool = False
    
    # Test specific params
    requests_per_user: int = 10  # For sequential tests
    
    @property
    def base_url(self) -> str:
        if self.target_env == TargetEnvironment.CUSTOM:
            return self.custom_url.rstrip('/')
        return self.target_env.value

@dataclass
class TestResultSummary:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_response_time: float = 0.0
    p95_response_time: float = 0.0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    llm_consistency_score: Optional[float] = None  # For Test 6
    messages_sent: int = 0
    total_file_size_mb: float = 0.0
    total_ingestion_time: float = 0.0
    
    # New Advanced Metrics
    successful_logouts: int = 0
    keyword_hits: int = 0
    consistency_stdev: Optional[float] = None
    latency_trend: List[float] = field(default_factory=list)
    lesson_saved: bool = False
