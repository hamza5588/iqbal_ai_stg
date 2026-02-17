from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.models.database_models import Base
import enum

class TestUserSet(Base):
    """Set of auto-generated users for load testing"""
    __tablename__ = 'load_test_user_sets'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)  # 'teacher' or 'student'
    user_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    users = relationship("TestUser", back_populates="user_set", cascade="all, delete-orphan")

class TestUser(Base):
    """Individual test user"""
    __tablename__ = 'load_test_users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_set_id = Column(Integer, ForeignKey('load_test_user_sets.id'), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    password = Column(String(255), nullable=False)
    real_user_id = Column(Integer, nullable=True)  # ID in the main users table
    is_active = Column(Boolean, default=True)
    
    # Relationships
    user_set = relationship("TestUserSet", back_populates="users")

class TestDocumentSet(Base):
    """Set of test documents for load testing"""
    __tablename__ = 'load_test_document_sets'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    documents = relationship("TestDocument", back_populates="doc_set", cascade="all, delete-orphan")

class TestDocument(Base):
    """Individual test document"""
    __tablename__ = 'load_test_documents'

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_set_id = Column(Integer, ForeignKey('load_test_document_sets.id'), nullable=False)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)  # Path on disk
    file_size_bytes = Column(Integer, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    doc_set = relationship("TestDocumentSet", back_populates="documents")

class LoadTestStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"

class LoadTestResult(Base):
    """Result of a load test execution"""
    __tablename__ = 'load_test_results'

    id = Column(Integer, primary_key=True, autoincrement=True)
    test_type = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False, default='pending')
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    # Configuration used for the test (JSON)
    config = Column(JSON, nullable=True)
    
    # Summary metrics (JSON)
    metrics = Column(JSON, nullable=True)
    
    # LLM Analysis
    llm_analysis = Column(Text, nullable=True)
    llm_analysis_created_at = Column(DateTime, nullable=True)
    
    # Relationships
    logs = relationship("LoadTestLog", back_populates="result", cascade="all, delete-orphan")

class LoadTestLog(Base):
    """Detailed log entry for a load test"""
    __tablename__ = 'load_test_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    result_id = Column(Integer, ForeignKey('load_test_results.id'), nullable=False)
    level = Column(String(20), nullable=False)  # INFO, ERROR, WARNING
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    result = relationship("LoadTestResult", back_populates="logs")
