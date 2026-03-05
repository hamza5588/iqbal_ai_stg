from __future__ import annotations

import os
import sqlite3
import tempfile
import json
import re
import logging
from pathlib import Path
from typing import Annotated, Any, Dict, Optional, TypedDict, List, Tuple

from pydantic import BaseModel, Field
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from app.utils.db import get_db
from app.utils.encryption import decrypt_api_key
from app.models.database_models import RAGPrompt, RAGThread, RAGChunk, SystemSettings
from app.config import Config
logger = logging.getLogger(__name__)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader
# Try to import fallback PDF loaders
try:
    from langchain_community.document_loaders import PyMuPDFLoader
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("PyMuPDFLoader not available. Install PyMuPDF for better PDF support.")

try:
    from langchain_community.document_loaders import PDFPlumberLoader
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    logger.warning("PDFPlumberLoader not available. Install pdfplumber for better PDF support.")

from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.documents import Document
# HuggingFace embeddings only (no OpenAI)
# Load torch.nn early to fix "name 'nn' is not defined" in sentence-transformers (staging/Docker)
try:
    import torch  # noqa: F401
    import torch.nn as _torch_nn  # noqa: F401 - ensures nn available for sentence-transformers
except ImportError:
    pass

try:
    from langchain_huggingface import HuggingFaceEmbeddings
    HUGGINGFACE_EMBEDDINGS_AVAILABLE = True
except ImportError:
    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings
        HUGGINGFACE_EMBEDDINGS_AVAILABLE = True
    except ImportError:
        HUGGINGFACE_EMBEDDINGS_AVAILABLE = False
        logger.warning("HuggingFace embeddings not available. Install langchain-huggingface or langchain-community.")
from app.utils.llm_factory import create_llm, get_chat_model
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
import requests

load_dotenv()

# -------------------
# Global rate limiter for Groq API calls
# -------------------
import time
from threading import Lock

class GroqRateLimiter:
    """Global rate limiter for Groq API to prevent 429 errors"""
    _instance = None
    _lock = Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(GroqRateLimiter, cls).__new__(cls)
                    cls._instance.last_request_time = 0
                    cls._instance.min_interval = 3.0  # Increased to 3 seconds between requests
                    cls._instance.consecutive_429_count = 0  # Track consecutive 429 errors
        return cls._instance
    
    def wait_if_needed(self):
        """Wait if needed to respect rate limits"""
        with self._lock:
            now = time.time()
            time_since_last = now - self.last_request_time
            
            # Increase delay if we've had recent 429 errors
            adjusted_interval = self.min_interval
            if self.consecutive_429_count > 0:
                adjusted_interval = self.min_interval * (1 + self.consecutive_429_count * 0.5)
                logger.warning(f"Rate limiter: increased interval to {adjusted_interval:.1f}s due to {self.consecutive_429_count} recent 429 errors")
            
            if time_since_last < adjusted_interval:
                wait_time = adjusted_interval - time_since_last
                logger.info(f"Rate limiting: waiting {wait_time:.2f} seconds before next Groq request")
                time.sleep(wait_time)
            self.last_request_time = time.time()
    
    def record_429_error(self):
        """Record a 429 error to adjust rate limiting"""
        with self._lock:
            self.consecutive_429_count += 1
            logger.warning(f"Recorded 429 error. Consecutive count: {self.consecutive_429_count}")
    
    def record_success(self):
        """Record a successful request to reset error count"""
        with self._lock:
            if self.consecutive_429_count > 0:
                logger.info(f"Resetting 429 error count after successful request")
            self.consecutive_429_count = 0

groq_rate_limiter = GroqRateLimiter()

# -------------------
# LLM instance cache to avoid recreating instances
# -------------------
_llm_cache = {}
_llm_cache_lock = Lock()

def get_cached_llm(user_id: int, api_key: str, provider: str):
    """Get or create a cached LLM instance for a user"""
    cache_key = f"{user_id}_{provider}_{api_key[:10] if api_key else 'none'}"
    
    with _llm_cache_lock:
        if cache_key not in _llm_cache:
            logger.debug(f"Creating new LLM instance for cache key: {cache_key[:20]}...")
            # Use new get_chat_model which respects admin/user settings
            try:
                _llm_cache[cache_key] = get_chat_model(user_id=user_id, timeout=120)
            except Exception as e:
                logger.warning(f"Error using get_chat_model, falling back to get_rag_llm: {str(e)}")
                _llm_cache[cache_key] = get_rag_llm(api_key=api_key, provider=provider)
        else:
            logger.debug(f"Reusing cached LLM instance for user {user_id}")
        return _llm_cache[cache_key]

# -------------------
# 1. LLM + embeddings
def _get_api_key_from_admin_settings():
    """
    Load active provider and API key from Admin Panel (SystemSettings).
    Used so RAG tools (e.g. outline extraction) use the same key as the chat UI.
    Returns (api_key or None, provider str).
    """
    try:
        db = get_db()
        setting = db.query(SystemSettings).filter(SystemSettings.key == 'active_provider').first()
        if not setting:
            setting = db.query(SystemSettings).filter(SystemSettings.key == 'llm_provider').first()
        provider = (setting.value if setting else os.getenv('LLM_PROVIDER', 'openai')).lower()
        key_name = f'{provider}_api_key'  # e.g. groq_api_key, openai_api_key
        key_setting = db.query(SystemSettings).filter(SystemSettings.key == key_name).first()
        if key_setting and key_setting.value:
            try:
                api_key = decrypt_api_key(key_setting.value)
                if api_key:
                    logger.debug(f"Using {provider} API key from Admin Panel for RAG tools")
                    return api_key, provider
            except Exception as e:
                logger.warning(f"Error decrypting API key from Admin Panel: {e}")
        # Fallback to environment
        if provider == 'groq':
            api_key = os.getenv('GROQ_API_KEY', '') or None
        else:
            api_key = os.getenv('OPENAI_API_KEY', '') or None
        return (api_key if api_key else None), provider
    except Exception as e:
        logger.warning(f"Could not load API key from Admin Panel: {e}")
        provider = os.getenv('LLM_PROVIDER', 'groq').lower()
        api_key = os.getenv('GROQ_API_KEY') if provider == 'groq' else os.getenv('OPENAI_API_KEY')
        return (api_key if api_key else None), provider


# -------------------
# Use dynamic LLM factory - RAG uses Groq or vLLM only (no OpenAI)
# Note: RAG service uses a global LLM instance, but individual requests should use user-specific API keys
def get_rag_llm(api_key=None, provider=None, user_id=None):
    """Get LLM for RAG service, using system settings or provided parameters.
    Prefers: (1) get_chat_model(user_id) then (2) Admin Panel API key from DB then (3) env."""
    # If user_id is provided, use get_chat_model which reads Admin Panel / user settings
    if user_id:
        try:
            return get_chat_model(user_id=user_id, timeout=120, temperature=0.7)
        except Exception as e:
            logger.warning(f"Error using get_chat_model with user_id {user_id}, falling back to Admin Panel key: {str(e)}")
    
    # Resolve provider from DB if not given (RAG uses groq or vllm only, not openai)
    if provider is None:
        try:
            db = get_db()
            setting = db.query(SystemSettings).filter(SystemSettings.key == 'active_provider').first()
            if setting:
                provider = setting.value.lower()
            else:
                setting = db.query(SystemSettings).filter(SystemSettings.key == 'llm_provider').first()
                provider = setting.value.lower() if setting else os.getenv('LLM_PROVIDER', 'groq').lower()
        except Exception:
            provider = os.getenv('LLM_PROVIDER', 'groq').lower()
    if provider == 'openai':
        provider = 'groq'
    
    # Use API key from Admin Panel (database) when not passed in, so tools use same key as chat
    if api_key is None and provider in ('groq', 'vllm'):
        admin_key, _ = _get_api_key_from_admin_settings()
        if admin_key:
            api_key = admin_key
    
    timeout_override = None
    if provider == 'groq':
        env_timeout = os.getenv('GROQ_TIMEOUT')
        timeout_override = int(env_timeout) if env_timeout else 120
    
    return create_llm(
        temperature=0.7,
        api_key=api_key if provider in ['groq', 'vllm'] else None,
        provider=provider,
        timeout=timeout_override
    )

"""
RAG service utilities.

Note: Avoid creating global LLM/embedding instances at import time because that
can require a Flask application context (e.g., database access for settings),
which is not available when Celery workers import this module on startup.
Instead, LLMs and embeddings are created lazily via helper functions.
"""

# Cache embedding model so we don't reload on every PDF query (~5+ seconds saved per request after first)
_rag_embeddings_cache: Optional[Any] = None
_rag_embeddings_lock = Lock()


def get_rag_embeddings():
    """Get HuggingFace embeddings (cached per process to avoid ~5s load on every query)."""
    global _rag_embeddings_cache
    if not HUGGINGFACE_EMBEDDINGS_AVAILABLE:
        raise ValueError(
            "HuggingFace embeddings are required. "
            "Please install: pip install langchain-huggingface sentence-transformers"
        )
    with _rag_embeddings_lock:
        if _rag_embeddings_cache is None:
            model = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
            _rag_embeddings_cache = HuggingFaceEmbeddings(model_name=model)
            logger.info("RAG embeddings model loaded and cached: %s", model)
        return _rag_embeddings_cache


def warmup_rag_embeddings() -> bool:
    """
    Load and cache the embedding model in this process so the first PDF query is fast.
    Call this when the user uploads a PDF (sync or async complete) or when they open a chat with a PDF.
    Safe to call multiple times; after the first call the cache is used.
    """
    try:
        get_rag_embeddings()
        return True
    except Exception as e:
        logger.warning("RAG embeddings warmup failed: %s", e)
        return False


# Batch embedding for faster ingestion (avoids sequential embed_query per doc)
EMBED_BATCH_SIZE = int(os.getenv("RAG_EMBED_BATCH_SIZE", "100"))  # docs per API call
EMBED_PARALLEL_BATCHES = int(os.getenv("RAG_EMBED_PARALLEL_BATCHES", "4"))  # concurrent batch requests (1 = sequential)


def _embed_documents_in_batches(
    embedding_model: Any,
    documents: List[Document],
    progress_callback: Optional[callable] = None,
    batch_size: int = EMBED_BATCH_SIZE,
    parallel_batches: int = EMBED_PARALLEL_BATCHES,
) -> Tuple[List[Tuple[str, List[float]]], List[Dict[str, Any]]]:
    """
    Embed documents in batches (and optionally in parallel) for faster ingestion.
    Returns (text_embeddings, metadatas) for Milvus insert.
    """
    if not documents:
        return [], []

    texts = [d.page_content for d in documents]
    metadatas = [dict(d.metadata) if d.metadata else {} for d in documents]
    n = len(texts)
    all_text_embeddings: List[Tuple[str, List[float]]] = []
    all_metadatas: List[Dict[str, Any]] = []

    def embed_batch(start: int) -> Tuple[List[Tuple[str, List[float]]], List[Dict[str, Any]]]:
        end = min(start + batch_size, n)
        batch_texts = texts[start:end]
        batch_metas = metadatas[start:end]
        batch_embeddings = embedding_model.embed_documents(batch_texts)
        return list(zip(batch_texts, batch_embeddings)), batch_metas

    if parallel_batches <= 1:
        # Sequential batching: one batch at a time
        for start in range(0, n, batch_size):
            te, meta = embed_batch(start)
            all_text_embeddings.extend(te)
            all_metadatas.extend(meta)
            if progress_callback:
                pct = 65 + int(15 * len(all_text_embeddings) / n)
                progress_callback("embeddings", min(pct, 79), f"Embedded {len(all_text_embeddings)}/{n} chunks...")
    else:
        # Parallel batching: run multiple batches concurrently, then reorder by start index
        batch_starts = list(range(0, n, batch_size))
        with ThreadPoolExecutor(max_workers=min(parallel_batches, len(batch_starts))) as executor:
            future_to_start = {executor.submit(embed_batch, start): start for start in batch_starts}
            completed = {}
            for future in as_completed(future_to_start):
                start = future_to_start[future]
                completed[start] = future.result()
        for start in batch_starts:
            te, meta = completed[start]
            all_text_embeddings.extend(te)
            all_metadatas.extend(meta)
        if progress_callback:
            progress_callback("embeddings", 78, f"Embedded {n} chunks (batched, parallel).")

    return all_text_embeddings, all_metadatas


# -------------------
# 2. Paths (no file-based metadata)
# -------------------
BASE_DIR = Path(__file__).parent.parent.parent
UPLOADED_FILES_DIR = BASE_DIR / "uploaded_files"
UPLOADED_FILES_DIR.mkdir(exist_ok=True)
MARKDOWN_EXPORTS_DIR = BASE_DIR / "markdown_exports"
MARKDOWN_EXPORTS_DIR.mkdir(exist_ok=True)
SPEED_LOG_PATH = BASE_DIR / "speed.txt"


def _write_speed_log(section: str, thread_id: Optional[str], steps: list[tuple[str, float]], started_at: float) -> None:
    """Append per-step timing to speed.txt for PDF query performance analysis."""
    if not steps:
        return
    try:
        # Create file with header if it doesn't exist
        if not SPEED_LOG_PATH.exists():
            with open(SPEED_LOG_PATH, "w", encoding="utf-8") as _f:
                _f.write(
                    "# RAG PDF query performance log\n"
                    "# Each section shows step timings (ms). Use this to find slow steps and improve response time.\n"
                    "# Sections: rag_tool (PDF retrieval), chat_node (LLM + tools).\n\n"
                )
        now_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "",
            "=" * 60,
            f"[{now_ts}] SECTION: {section}  thread_id={thread_id or 'n/a'}",
            "-" * 40,
        ]
        prev = started_at
        for label, ts in steps:
            ms = (ts - prev) * 1000
            lines.append(f"  {label}: {ms:.1f} ms")
            prev = ts
        total_ms = (prev - started_at) * 1000
        lines.append(f"  TOTAL: {total_ms:.1f} ms")
        lines.append("")
        with open(SPEED_LOG_PATH, "a", encoding="utf-8") as _f:
            _f.write("\n".join(lines) + "\n")
    except Exception as e:
        logger.debug("Could not write speed log: %s", e)


def _get_thread_metadata_from_db(thread_id: str) -> Optional[Dict[str, Any]]:
    """Get thread metadata from database (replaces _THREAD_METADATA)."""
    try:
        db = get_db()
        thread = db.query(RAGThread).filter_by(thread_id=str(thread_id)).first()
        if not thread:
            return None
        return {
            "filename": thread.filename,
            "num_pages": thread.num_pages,
            "pages": thread.num_pages,
            "documents": thread.num_pages,
            "doc_count": thread.doc_count,
            "chunks": None,
            "lesson_finalized": getattr(thread, "lesson_finalized", False),
            "last_lesson_text": getattr(thread, "last_lesson_text", ""),
            "lesson_title": getattr(thread, "lesson_title", ""),
        }
    except Exception as e:
        logger.warning("Error getting thread metadata from DB: %s", e)
        return None


def _get_user_id_for_thread(thread_id: str) -> Optional[int]:
    """Get user_id from DB (RAGThread). Never infer from thread_id string."""
    if not thread_id:
        return None
    try:
        db = get_db()
        thread = db.query(RAGThread).filter_by(thread_id=str(thread_id)).first()
        return thread.user_id if thread else None
    except Exception as e:
        logger.warning("_get_user_id_for_thread error: %s", e)
        return None


def _get_rag_prompt(user_id: Optional[int], thread_id: Optional[str] = None) -> Optional[str]:
    """
    Get custom RAG prompt for user from database.
    Prompts are user-level and apply to all threads for that user.
    Returns None if no custom prompt is set (will use default).
    """
    if not user_id:
        return None
    
    try:
        db = get_db()
        # Get user-specific prompt (applies to all threads)
        prompt = db.query(RAGPrompt).filter(
            RAGPrompt.user_id == user_id,
            RAGPrompt.thread_id.is_(None)
        ).order_by(RAGPrompt.updated_at.desc()).first()
        
        if prompt:
            return prompt.prompt
        
        return None
    except Exception as e:
        logger.error(f"Error retrieving RAG prompt: {str(e)}")
        return None


def _get_retriever(thread_id: Optional[str], user_id: Optional[int] = None, steps_list: Optional[list] = None):
    """
    Get a retriever for a specific thread using Milvus.
    Returns an object with invoke(query) -> List[Document].
    If steps_list is provided, timing for embed_query, vector_search, fetch_chunks, build_docs is appended.
    """
    if not thread_id:
        return None
    if user_id is None:
        user_id = _get_user_id_for_thread(thread_id)
    if user_id is None:
        return None

    import os
    from app.utils.rag_vectorstore import similarity_search, hybrid_search, fetch_chunks_by_ids
    embeddings = get_rag_embeddings()

    class VectorRetriever:
        def __init__(self, thread_id: str, user_id: int, steps_list: Optional[list] = None):
            self.thread_id = str(thread_id)
            self.user_id = int(user_id)
            self.steps_list = steps_list
            # Feature flag so we can safely switch between pure semantic and hybrid.
            self.use_hybrid = os.getenv("USE_HYBRID_RAG", "false").lower() in ("true", "1", "yes")

        def invoke(self, query: str) -> List[Document]:
            def _step(label: str) -> None:
                if self.steps_list is not None:
                    self.steps_list.append((label, time.perf_counter()))

            _step("retriever_embed_query_start")
            query_vector = embeddings.embed_query(query)
            _step("retriever_vector_search_start")
            if self.use_hybrid:
                print("[RAG] Using HYBRID retrieval (semantic + lexical) for thread_id=%s" % self.thread_id)
                logger.info(
                    "RAG retrieval: using HYBRID (semantic + lexical) for thread_id=%s query_len=%d",
                    self.thread_id, len(query),
                )
                results = hybrid_search(
                    query=query,
                    query_vector=query_vector,
                    thread_id=self.thread_id,
                    user_id=self.user_id,
                    k=12,
                )
                print("[RAG] hybrid_search returned %d chunks" % len(results))
                logger.info(
                    "RAG retrieval: hybrid_search returned %d chunks for thread_id=%s",
                    len(results), self.thread_id,
                )
            else:
                print("[RAG] Using SEMANTIC-ONLY retrieval (vector) for thread_id=%s" % self.thread_id)
                logger.info(
                    "RAG retrieval: using SEMANTIC-ONLY (vector) for thread_id=%s query_len=%d",
                    self.thread_id, len(query),
                )
                results = similarity_search(
                    query_vector=query_vector,
                    thread_id=self.thread_id,
                    user_id=self.user_id,
                    k=12,
                )
                logger.info(
                    "RAG retrieval: similarity_search returned %d chunks for thread_id=%s",
                    len(results), self.thread_id,
                )
            _step("retriever_fetch_chunks_start")
            chunk_ids = [r["chunk_id"] for r in results if r.get("chunk_id") is not None]
            chunk_map = fetch_chunks_by_ids(chunk_ids) if chunk_ids else {}
            _step("retriever_build_docs_start")
            docs = []
            for r in results:
                cid = r.get("chunk_id")
                c = chunk_map.get(cid) if cid else {}
                doc = Document(
                    page_content=c.get("text", ""),
                    metadata={"source": c.get("source", ""), "page": r.get("page", 0), "chunk_index": r.get("chunk_index", 0)},
                )
                docs.append(doc)
            _step("retriever_done")
            logger.info("VectorRetriever: returned %d documents for thread_id=%s", len(docs), self.thread_id)
            return docs

    use_hybrid = os.getenv("USE_HYBRID_RAG", "false").lower() in ("true", "1", "yes")
    print("[RAG] Retriever created: use_hybrid=%s (set USE_HYBRID_RAG=true for hybrid)" % use_hybrid)
    logger.info(
        "RAG retriever created thread_id=%s user_id=%s use_hybrid=%s (USE_HYBRID_RAG env)",
        thread_id, user_id, use_hybrid,
    )
    return VectorRetriever(thread_id, user_id, steps_list)


def ingest_pdf(
    file_bytes: bytes,
    thread_id: str,
    filename: Optional[str] = None,
    progress_callback: Optional[callable] = None,
    user_id: Optional[int] = None,
) -> dict:
    """
    Ingest PDF: chunk text -> PostgreSQL (RAGChunk), vectors -> Milvus/Chroma.
    user_id must be passed (never inferred from thread_id).
    """
    def _send_progress(step: str, progress: int, message: str):
        if progress_callback:
            try:
                progress_callback(step, progress, message)
            except Exception as e:
                logger.warning(f"Error sending progress update: {e}")

    if not file_bytes:
        raise ValueError("No bytes received for ingestion.")

    _start_time = time.time()
    _send_progress("init", 5, "Initializing PDF processing...")

    thread_id_str = str(thread_id)
    if user_id is None:
        user_id = _get_user_id_for_thread(thread_id_str)
    if user_id is None:
        raise ValueError("user_id is required; pass explicitly or ensure thread exists in DB")

    safe_filename = filename or f"document_{thread_id_str}.pdf"
    safe_filename = "".join(c for c in safe_filename if c.isalnum() or c in "._- ")
    file_path = UPLOADED_FILES_DIR / f"{thread_id_str}_{safe_filename}"

    _send_progress("saving", 10, "Saving PDF file...")
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
        temp_file.write(file_bytes)
        temp_path = temp_file.name

    try:
        _send_progress("loading", 15, "Reading PDF document...")
        
        # Try multiple PDF loaders as fallback
        docs = None
        loader_used = None
        last_error = None
        
        # Try 1: PyPDFLoader (fastest, works for most PDFs)
        try:
            loader = PyPDFLoader(temp_path)
            docs = loader.load()
            loader_used = "PyPDFLoader"
            logger.info(f"Successfully loaded PDF using PyPDFLoader")
        except Exception as e1:
            last_error = str(e1)
            logger.warning(f"PyPDFLoader failed: {last_error}")
            docs = None
        
        # Try 2: PyMuPDFLoader (better for complex PDFs, handles more formats)
        if not docs or (docs and len([d for d in docs if d.page_content and d.page_content.strip()]) == 0):
            if PYMUPDF_AVAILABLE:
                try:
                    _send_progress("loading", 18, "Trying alternative PDF loader (PyMuPDF)...")
                    loader = PyMuPDFLoader(temp_path)
                    docs = loader.load()
                    loader_used = "PyMuPDFLoader"
                    logger.info(f"Successfully loaded PDF using PyMuPDFLoader (fallback)")
                except Exception as e2:
                    last_error = str(e2)
                    logger.warning(f"PyMuPDFLoader failed: {last_error}")
                    if not docs:
                        docs = None
            else:
                logger.debug("PyMuPDFLoader not available, skipping fallback")
        
        # Try 3: PDFPlumberLoader (good for tables and complex layouts)
        if not docs or (docs and len([d for d in docs if d.page_content and d.page_content.strip()]) == 0):
            if PDFPLUMBER_AVAILABLE:
                try:
                    _send_progress("loading", 20, "Trying alternative PDF loader (PDFPlumber)...")
                    loader = PDFPlumberLoader(temp_path)
                    docs = loader.load()
                    loader_used = "PDFPlumberLoader"
                    logger.info(f"Successfully loaded PDF using PDFPlumberLoader (fallback)")
                except Exception as e3:
                    last_error = str(e3)
                    logger.warning(f"PDFPlumberLoader failed: {last_error}")
                    if not docs:
                        docs = None
            else:
                logger.debug("PDFPlumberLoader not available, skipping fallback")
        
        # Final check - all loaders failed
        if not docs:
            error_msg = (
                "Failed to load PDF with all available loaders (PyPDFLoader"
            )
            if PYMUPDF_AVAILABLE:
                error_msg += ", PyMuPDFLoader"
            if PDFPLUMBER_AVAILABLE:
                error_msg += ", PDFPlumberLoader"
            error_msg += (
                "). The PDF might be corrupted, password-protected, or image-based (scanned). "
                "For scanned PDFs, OCR support is required."
            )
            if last_error:
                error_msg += f" Last error: {last_error}"
            raise ValueError(error_msg)

        num_pages = len(docs)
        if num_pages == 0:
            raise ValueError("PDF appears to be empty or could not be loaded. No pages found.")

        _send_progress("validating", 25, f"Validating {num_pages} pages (loaded with {loader_used})...")

        valid_pages = [d for d in docs if d.page_content and d.page_content.strip()]
        if len(valid_pages) == 0:
            # Check if PDF might be scanned (image-based)
            scanned_hint = ""
            try:
                import fitz  # PyMuPDF
                pdf_doc = fitz.open(temp_path)
                has_images = any(len(page.get_images()) > 0 for page in pdf_doc)
                pdf_doc.close()
                
                if has_images:
                    scanned_hint = (
                        " This PDF appears to contain images and might be scanned. "
                        "OCR support is required for scanned documents."
                    )
            except (ImportError, Exception):
                pass  # PyMuPDF not available or error checking images
            
            raise ValueError(
                f"PDF loaded with {loader_used} but contains no extractable text content.{scanned_hint}"
            )

        _send_progress("metadata", 30, "Adding metadata to pages...")
        for i, doc in enumerate(docs):
            doc.metadata = {
                **(doc.metadata or {}),
                "thread_id": thread_id_str,
                "user_id": user_id,
                "filename": filename or os.path.basename(temp_path),
                "page": i + 1,            # 1-indexed
                "page_number": i + 1,     # alias
                "page_zero_index": i,     # 0-indexed
                "total_pages": num_pages,
            }

        # Export extracted text as markdown for user download (## Page N + content per page)
        safe_base = (safe_filename or "document").rstrip(".pdf").rstrip(".PDF") or "document"
        md_filename = f"{thread_id_str}_{safe_base}.md"
        md_path = MARKDOWN_EXPORTS_DIR / md_filename
        md_parts = []
        for doc in docs:
            page_num = doc.metadata.get("page") or doc.metadata.get("page_number", "?")
            md_parts.append(f"## Page {page_num}\n\n{doc.page_content or ''}\n\n")
        try:
            md_path.write_text("\n".join(md_parts), encoding="utf-8")
            logger.info("Saved PDF text as markdown: %s", md_path)
        except Exception as e:
            logger.warning("Could not save markdown export: %s", e)

        _send_progress("splitting", 40, "Splitting document into chunks...")
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1600,
            chunk_overlap=600,
            separators=["\n\n", "\n", " ", ""],
        )
        chunks = splitter.split_documents(docs)
        _send_progress("splitting", 50, f"Created {len(chunks)} text chunks from {num_pages} pages")

        _send_progress("chunk_metadata", 55, "Enriching chunk metadata...")
        for i, c in enumerate(chunks):
            page_num = c.metadata.get("page") or c.metadata.get("page_number", "unknown")
            page_zero_idx = None
            try:
                if isinstance(page_num, (int, float)):
                    page_zero_idx = int(page_num) - 1
                elif isinstance(page_num, str) and page_num.isdigit():
                    page_zero_idx = int(page_num) - 1
            except (ValueError, TypeError):
                pass

            c.metadata = {
                **(c.metadata or {}),
                "chunk_length": len(c.page_content),
                "source_pdf": filename or os.path.basename(temp_path),
                "thread_id": thread_id_str,
                "user_id": user_id,
                "page": page_num,
                "page_number": page_num,
                "page_zero_index": page_zero_idx if page_zero_idx is not None else c.metadata.get("page_zero_index"),
                "num_pages": num_pages,
                "total_pages": num_pages,
                "type": "chunk",  # OPTIONAL but helpful
            }

            if (i + 1) % 50 == 0:
                _send_progress(
                    "chunk_metadata",
                    55 + int((i + 1) / max(len(chunks), 1) * 5),
                    f"Processing chunk {i + 1}/{len(chunks)}...",
                )

        _send_progress("embeddings", 60, "Creating embeddings for chunks (batched)...")
        current_embeddings = get_rag_embeddings()
        text_embeddings, embed_metadatas = _embed_documents_in_batches(
            current_embeddings, chunks, _send_progress
        )
        _send_progress("embeddings", 70, f"Embedded {len(text_embeddings)} chunks.")
        vectors = [te[1] for te in text_embeddings]
        texts = [te[0] for te in text_embeddings]

        _send_progress("postgres", 72, "Storing chunk text in PostgreSQL...")
        from datetime import datetime as dt
        db = get_db()
        try:
            thread_row = db.query(RAGThread).filter_by(thread_id=thread_id_str).first()
            if not thread_row:
                thread_row = RAGThread(
                    user_id=user_id,
                    thread_id=thread_id_str,
                    name=f"Thread {dt.utcnow().strftime('%Y-%m-%d %H:%M')}",
                    filename=filename or safe_filename,
                )
                db.add(thread_row)
                db.commit()
                db.refresh(thread_row)
                logger.info("Created RAGThread %s for ingestion", thread_id_str)

            for i, (text, meta) in enumerate(zip(texts, embed_metadatas)):
                rc = RAGChunk(
                    thread_id=thread_id_str,
                    user_id=user_id,
                    document_id=None,
                    chunk_index=i,
                    page=int(meta.get("page") or meta.get("page_number") or 0),
                    text=text,
                    source=meta.get("source_pdf") or safe_filename,
                )
                db.add(rc)
            db.commit()
            chunk_rows = db.query(RAGChunk).filter(
                RAGChunk.thread_id == thread_id_str,
                RAGChunk.user_id == user_id,
            ).order_by(RAGChunk.chunk_index).all()
            chunk_ids = [r.id for r in chunk_rows]
            if len(chunk_ids) != len(chunks):
                db.rollback()
                raise ValueError("Chunk insert count mismatch")
        except Exception as e:
            logger.error("Failed to store chunks in PostgreSQL: %s", e)
            db.rollback()
            raise
        _send_progress("postgres", 78, f"Stored {len(chunk_ids)} chunks in PostgreSQL.")

        _send_progress("vector_store", 80, "Inserting vectors...")
        from app.utils.rag_vectorstore import insert_chunks, ensure_collection, EMBEDDING_MODEL_NAME, EMBEDDING_DIM

        pages = [int(m.get("page") or m.get("page_number") or 0) for m in embed_metadatas]
        chunk_indices = list(range(len(chunks)))

        ensure_collection()
        insert_chunks(
            vectors=vectors,
            thread_id=thread_id_str,
            user_id=user_id,
            document_id=None,
            chunk_ids=chunk_ids,
            pages=pages,
            chunk_indices=chunk_indices,
        )
        _send_progress("vector_store", 85, f"Inserted {len(chunks)} vectors.")

        try:
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Deleted uploaded PDF file: {file_path}")
        except Exception as e:
            logger.error(f"Failed to delete uploaded PDF file {file_path}: {e}")

        _send_progress("cleanup", 95, "Cleaning up temporary files...")

        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
                logger.info(f"Successfully deleted temporary PDF file: {temp_path}")
        except OSError as e:
            logger.warning(f"Failed to delete temporary PDF file {temp_path}: {e}")

        _elapsed_seconds = round(time.time() - _start_time, 2)
        _send_progress("complete", 100, f"PDF processing complete! Processed {num_pages} pages in {_elapsed_seconds}s.")

        return {
            "thread_id": thread_id_str,
            "filename": filename or safe_filename,
            "documents": num_pages,
            "num_pages": num_pages,
            "pages": num_pages,
            "chunks": len(chunks),
            "embedding_model": EMBEDDING_MODEL_NAME,
            "embedding_dim": EMBEDDING_DIM,
            "markdown_filename": md_filename,
            "processing_time_seconds": _elapsed_seconds,
        }

    finally:
        # Cleanup temporary file (used for PDF loading)
        try:
            if "temp_path" in locals() and os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError as e:
            logger.warning(f"Failed to delete temporary PDF file in finally block {temp_path}: {e}")





# -------------------
# 4. Tools
# -------------------



@tool
def calculator(first_num: float, second_num: float, operation: str) -> dict:
    """
    Perform a basic arithmetic operation on two numbers.
    Supported operations: add, sub, mul, div
    """
    try:
        if operation == "add":
            result = first_num + second_num
        elif operation == "sub":
            result = first_num - second_num
        elif operation == "mul":
            result = first_num * second_num
        elif operation == "div":
            if second_num == 0:
                return {"error": "Division by zero is not allowed"}
            result = first_num / second_num
        else:
            return {"error": f"Unsupported operation '{operation}'"}

        return {
            "first_num": first_num,
            "second_num": second_num,
            "operation": operation,
            "result": result,
        }
    except Exception as e:
        return {"error": str(e)}




@tool
def get_page_tool(page: int, thread_id: str) -> dict:
    """
    Get the content of a specific page from the uploaded PDF for this chat thread.
    Page numbers: 0 or 1 both refer to the first page (page 1 in the PDF).
    Always include the thread_id when calling this tool.
    """
    logger.info(f"get_page_tool called: page={page}, thread_id={thread_id}")
    
    # Extract user_id from thread_id
    user_id = _get_user_id_for_thread(thread_id) if thread_id else None
    if user_id is None:
        return {
            "error": f"Could not extract user_id from thread_id: {thread_id}",
            "thread_id": thread_id,
            "page_requested": page,
            "page_resolved": None,
            "chunks_found": 0,
        }
    
    # Map UI page=0 to page=1 (ingestion uses 1-indexed pages)
    original_page = page
    if page == 0:
        resolved_page = 1
    else:
        resolved_page = page
    
    logger.info("get_page_tool: page_requested=%s, page_resolved=%s, user_id=%s", original_page, resolved_page, user_id)

    from app.utils.rag_vectorstore import query_chunks_by_page
    results = query_chunks_by_page(thread_id=thread_id, user_id=user_id, page=resolved_page)

    if not results:
        return {
            "error": f"No content found for page {resolved_page} (requested as page {original_page}).",
            "thread_id": thread_id,
            "page_requested": original_page,
            "page_resolved": resolved_page,
            "chunks_found": 0,
        }

    results.sort(key=lambda x: x.get("chunk_index", 0))
    content = [r.get("text", "") for r in results]
    metadata = [{"source": r.get("source"), "page": r.get("page"), "chunk_index": r.get("chunk_index")} for r in results]
    
    return {
        "thread_id": thread_id,
        "page_requested": original_page,
        "page_resolved": resolved_page,
        "chunks_found": len(results),
        "content": content,
        "metadata": metadata,
    }

import re
def _extract_topics_with_ai(page_docs: List[Document], user_id: int, thread_id: str) -> dict:
    """
    Helper function to use AI for extracting topics from document pages.
    
    Strategy:
    1. First, check early pages (1-10) for Table of Contents using AI
    2. If TOC found, extract topics from TOC
    3. If no TOC, scan all pages in batches to extract headings
    """
    try:
        # Get LLM instance for topic extraction (use user_id so admin/system API key is used)
        user_llm = get_rag_llm(user_id=user_id)
        
        # Phase 1: Check for TOC in early pages (first 10 pages)
        early_pages = [d for d in page_docs[:10] if d.metadata.get("page", 0) <= 10]
        
        if early_pages:
            # Combine first few pages for TOC detection
            toc_candidates = []
            for d in early_pages[:5]:  # Check first 5 pages
                page_num = d.metadata.get("page") or d.metadata.get("page_number", "?")
                text = d.page_content or ""
                if len(text) > 100:  # Only check pages with substantial content
                    toc_candidates.append(f"--- Page {page_num} ---\n{text[:2000]}")  # Limit text per page
            
            if toc_candidates:
                toc_check_prompt = f"""Analyze the following pages from a document to determine if they contain a Table of Contents (TOC) or outline.

Pages to analyze:
{chr(10).join(toc_candidates)}

Instructions:
1. Determine if any of these pages contain a Table of Contents, Contents page, Outline, or Agenda
2. If a TOC is found, extract ALL topics/sections listed in it
3. Return your response as a JSON object with this structure:
{{
    "has_toc": true/false,
    "toc_page": page number where TOC was found (or null),
    "topics": ["topic 1", "topic 2", ...]  // List of all topics from TOC, empty if no TOC
}}

Important:
- Only extract actual topics/sections from the TOC, not regular text
- Remove page numbers, dots, and formatting from topic names
- Keep topic names clean and meaningful
- If no TOC is found, set "has_toc": false and "topics": []
"""
                
                try:
                    response = user_llm.invoke(toc_check_prompt)
                    response_text = response.content if hasattr(response, 'content') else str(response)
                    
                    # Try to extract JSON from response
                    import json
                    # Look for JSON object in the response (more flexible pattern)
                    json_patterns = [
                        r'\{[^{}]*"has_toc"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',  # Nested objects
                        r'\{[^}]*"has_toc"[^}]*\}',  # Simple object
                    ]
                    
                    toc_result = None
                    for pattern in json_patterns:
                        json_match = re.search(pattern, response_text, re.DOTALL)
                        if json_match:
                            try:
                                toc_result = json.loads(json_match.group(0))
                                break
                            except json.JSONDecodeError:
                                continue
                    
                    # If no JSON found, try to parse as markdown code block
                    if not toc_result:
                        code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
                        if code_block:
                            try:
                                toc_result = json.loads(code_block.group(1))
                            except json.JSONDecodeError:
                                pass
                    
                    if toc_result and toc_result.get("has_toc") and toc_result.get("topics"):
                        topics = [{"topic": t.strip(), "page": toc_result.get("toc_page")} 
                                 for t in toc_result.get("topics", []) if t.strip()]
                        if topics:
                            logger.info(f"Found TOC with {len(topics)} topics using AI")
                            return {
                                "topics": topics,
                                "method": "ai_toc_extraction",
                                "topics_count": len(topics)
                            }
                except Exception as e:
                    logger.warning(f"Error in AI TOC extraction: {e}, falling back to heading extraction")
        
        # Phase 2: No TOC found, extract headings from all pages using AI
        logger.info("No TOC found, extracting headings from all pages using AI")
        
        # Process pages in batches to avoid token limits
        batch_size = 3  # Process 3 pages at a time
        all_headings = []
        seen_headings = set()
        
        for i in range(0, len(page_docs), batch_size):
            batch = page_docs[i:i + batch_size]
            batch_texts = []
            batch_pages = []
            
            for d in batch:
                page_num = d.metadata.get("page") or d.metadata.get("page_number", "?")
                text = d.page_content or ""
                if text:
                    batch_texts.append(f"--- Page {page_num} ---\n{text[:3000]}")  # Limit to 3000 chars per page
                    batch_pages.append(page_num)
            
            if not batch_texts:
                continue
            
            heading_extraction_prompt = f"""Analyze the following pages from a document and identify ALL section headings, chapter titles, and major topics.

Pages to analyze:
{chr(10).join(batch_texts)}

Instructions:
1. Identify section headings, chapter titles, subsection headings, and major topics
2. Ignore regular paragraph text, body content, and sentences
3. Only extract actual headings/titles that indicate document structure
4. Return your response as a JSON array of heading objects:
[
    {{"heading": "Heading text", "page": page_number}},
    {{"heading": "Another heading", "page": page_number}},
    ...
]

Important:
- Extract only headings/titles, NOT regular text or sentences
- Clean heading text (remove extra spaces, formatting)
- Include the page number where each heading was found
- If no headings found on these pages, return an empty array []
- Do not include author names, page numbers alone, or footer/header text
"""
            
            try:
                response = user_llm.invoke(heading_extraction_prompt)
                response_text = response.content if hasattr(response, 'content') else str(response)
                
                # Extract JSON array from response
                import json
                headings_batch = []
                
                # Try multiple patterns to extract JSON array
                json_patterns = [
                    r'\[[^\]]*\{[^}]+\}[^\]]*\]',  # Array with objects
                    r'\[[^\]]*"heading"[^\]]*\]',  # Array with heading strings
                ]
                
                for pattern in json_patterns:
                    json_match = re.search(pattern, response_text, re.DOTALL)
                    if json_match:
                        try:
                            headings_batch = json.loads(json_match.group(0))
                            break
                        except json.JSONDecodeError:
                            continue
                
                # If no JSON found, try markdown code block
                if not headings_batch:
                    code_block = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', response_text, re.DOTALL)
                    if code_block:
                        try:
                            headings_batch = json.loads(code_block.group(1))
                        except json.JSONDecodeError:
                            pass
                
                # Process extracted headings
                if headings_batch:
                    for item in headings_batch:
                        if isinstance(item, dict):
                            heading_text = item.get("heading", "").strip()
                            page_num = item.get("page")
                            
                            if heading_text and len(heading_text) > 2:
                                # Normalize and deduplicate
                                heading_lower = heading_text.lower().strip()
                                if heading_lower not in seen_headings:
                                    seen_headings.add(heading_lower)
                                    all_headings.append({
                                        "topic": heading_text,
                                        "page": page_num
                                    })
                else:
                    # Fallback: try to extract headings from plain text response
                    lines = response_text.split('\n')
                    for line in lines:
                        line = line.strip()
                        if line and (line.startswith('-') or line.startswith('*') or 
                                    re.match(r'^\d+[\.\)]', line)):
                            # Extract heading from list item
                            heading = re.sub(r'^[-*\d+\.\)\s]+', '', line).strip()
                            if heading and len(heading) > 2:
                                heading_lower = heading.lower()
                                if heading_lower not in seen_headings:
                                    seen_headings.add(heading_lower)
                                    all_headings.append({"topic": heading, "page": None})
            except Exception as e:
                logger.warning(f"Error extracting headings from batch {i//batch_size + 1}: {e}")
                continue
        
        # Sort by page number if available
        def sort_key(item):
            page = item.get("page")
            if page is None:
                return 10**9
            try:
                return int(page)
            except:
                return 10**9
        
        all_headings.sort(key=sort_key)
        
        logger.info(f"AI extracted {len(all_headings)} headings from document")
        
        return {
            "topics": all_headings,
            "method": "ai_heading_extraction",
            "topics_count": len(all_headings)
        }
        
    except Exception as e:
        logger.error(f"Error in AI topic extraction: {e}")
        raise


@tool
def list_topics_whole_doc_tool(thread_id: str) -> dict:
    """
    Extract a high-level outline of a document by identifying section titles,
    headings, and topics across the entire PDF using AI analysis.

    Use this tool when the user asks for:
    - what topic(s) are covered / what topics does the document cover
    - a list of topics or sections in the document
    - the document outline or structure
    - headings or major sections
    - what the document covers at a high level
    - a table of contents (explicit or inferred)
    - navigation help such as "jump to section" or "what sections are there"

    This tool uses AI to intelligently extract topics:
    1. First checks for Table of Contents (TOC) in early pages
    2. If TOC found, extracts topics from it
    3. If no TOC, scans all pages to identify headings and major topics
    4. Returns a clean, deduplicated list of topics with page numbers

    Parameters:
    - thread_id (str): The conversation thread identifier associated with the uploaded PDF.

    Returns:
    - dict with keys:
        - "topics": list of topic objects with "topic" (str) and "page" (int) keys
        - "topics_count": total number of unique topics found
        - "method": extraction method used ("ai_toc_extraction" or "ai_heading_extraction")
        - "chunks_scanned": number of document pages analyzed
    """
    user_id = _get_user_id_for_thread(thread_id)
    if user_id is None:
        return {"error": f"Could not extract user_id from thread_id: {thread_id}"}

    from app.utils.rag_vectorstore import query_all_chunks

    all_chunks = query_all_chunks(thread_id=str(thread_id), user_id=user_id)
    if not all_chunks:
        return {"error": "No document pages found for this thread. Upload a PDF first."}

    # Group chunks by page and build Document per page for AI extraction
    from collections import defaultdict
    by_page = defaultdict(list)
    for c in all_chunks:
        p = c.get("page") or 0
        try:
            p = int(p)
        except (ValueError, TypeError):
            p = 0
        by_page[p].append(c)
    for k in by_page:
        by_page[k].sort(key=lambda x: x.get("chunk_index", 0))

    page_docs = []
    for p in sorted(by_page.keys()):
        chunks = by_page[p]
        text = "\n\n".join(c.get("text", "") for c in chunks)
        doc = Document(
            page_content=text,
            metadata={"page": p, "page_number": p, "source": chunks[0].get("source", "") if chunks else ""},
        )
        page_docs.append(doc)

    if not page_docs:
        return {"error": "No document pages found for this thread."}

    # Use AI to extract topics
    try:
        result = _extract_topics_with_ai(page_docs, user_id, thread_id)
        result["thread_id"] = thread_id
        result["chunks_scanned"] = len(page_docs)
        return result
    except Exception as e:
        logger.error(f"Error in AI topic extraction: {e}")
        return {
            "error": f"Failed to extract topics using AI: {str(e)}",
            "thread_id": thread_id,
            "topics": [],
            "topics_count": 0,
            "chunks_scanned": len(page_docs)
        }

_WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)

def _count_words(text: str) -> int:
    """
    Count words in text with preprocessing:
    - Remove extra whitespace (normalize to single spaces)
    - Remove # symbols (hashtags/pound symbols)
    - Strip leading/trailing whitespace
    """
    if not text:
        return 0
    
    # Preprocess: Remove # symbols first
    text = text.replace('#', '')
    
    # Normalize whitespace: replace multiple spaces/tabs/newlines with single space
    text = re.sub(r'\s+', ' ', text)
    
    # Strip leading/trailing whitespace
    text = text.strip()
    
    if not text:
        return 0
    
    # Count words using word boundary regex
    words = _WORD_RE.findall(text)
    return len(words)




@tool
def count_pdf_words_tool(
    thread_id: str,
    page: Optional[int] = None,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
    include_per_page: bool = False
) -> dict:
    """Count words in uploaded PDF for this thread. Supports whole doc, single page, or page range."""
    user_id = _get_user_id_for_thread(thread_id)
    if user_id is None:
        return {"error": f"Could not extract user_id from thread_id: {thread_id}"}

    from app.utils.rag_vectorstore import query_all_chunks

    thread_id_str = str(thread_id)

    def norm(p: Optional[int]) -> Optional[int]:
        if p is None:
            return None
        try:
            p = int(p)
        except Exception:
            return None
        return 1 if p == 0 else p

    page_n = norm(page)
    start_n = norm(start_page)
    end_n = norm(end_page)

    if page_n is not None:
        start_n, end_n = page_n, page_n
    if start_n is not None and end_n is None:
        end_n = start_n
    if end_n is not None and start_n is None:
        start_n = 1

    all_chunks = query_all_chunks(thread_id=thread_id_str, user_id=user_id)
    if not all_chunks:
        return {"error": "No chunks found for this thread. Upload a PDF first."}

    # Group chunks by page
    pages_seen = set()
    for c in all_chunks:
        p = c.get("page")
        try:
            pages_seen.add(int(p) if p is not None else 0)
        except (ValueError, TypeError):
            pass

    if not pages_seen:
        return {"error": "No page data found in chunks."}

    max_page = max(pages_seen)
    if start_n is None:
        start_n = 1
    if end_n is None:
        end_n = max_page

    total = 0
    per_page = {}
    for p in range(start_n, end_n + 1):
        page_chunks = [c for c in all_chunks if (c.get("page") or 0) == p]
        if not page_chunks:
            continue
        page_chunks.sort(key=lambda x: x.get("chunk_index", 0))
        text = " ".join(c.get("text", "") for c in page_chunks)
        wc = _count_words(text)
        total += wc
        per_page[p] = wc

    meta = _get_thread_metadata_from_db(thread_id_str) or {}
    num_pages = meta.get("num_pages") or meta.get("pages") or meta.get("documents")

    out = {
        "thread_id": thread_id,
        "source_file": meta.get("filename"),
        "num_pages": num_pages,
        "page": page_n,
        "start_page": start_n,
        "end_page": end_n,
        "total_words": total,
        "note": "Count is based on extracted text; scanned PDFs may require OCR for accurate word counts."
    }
    if include_per_page:
        out["per_page_words"] = dict(sorted(per_page.items(), key=lambda x: x[0]))
    return out

@tool
def count_words_in_text_tool(text: str, label: str = "text") -> dict:
    """Count words in a given text."""
    return {"label": label, "words": _count_words(text)}




def _strip_metadata_like_lines(text: str) -> str:
    """Remove lines that look like PDF/internal metadata so they are not shown to the user."""
    if not text or not text.strip():
        return text
    skip_phrases = (
        "metadata notes", "the page is part of", "duplicated in two chunks",
        "pdf-xchange", "created using", "timestamps from", "windows 10",
        "the content is duplicated", "likely due to pdf formatting",
        "for deeper insights", "for specific applications", "feel free to ask",
    )
    lines = text.split("\n")
    kept = []
    for line in lines:
        lower = line.strip().lower()
        if not lower:
            kept.append(line)
            continue
        if any(phrase in lower for phrase in skip_phrases):
            continue
        kept.append(line)
    return "\n".join(kept).strip() or text


@tool
def rag_tool(query: str, thread_id: Optional[str] = None):
    """
    Retrieve relevant information from the uploaded PDF for this chat thread.
    Always include the thread_id when calling this tool.
    Returns content-only text for the LLM (no internal metadata).
    """
    rag_steps = []
    rag_started = time.perf_counter()

    def _rag_step(label: str) -> None:
        rag_steps.append((label, time.perf_counter()))

    logger.info(f"rag_tool called: query='{query[:100]}...', thread_id={thread_id}")
    _rag_step("rag_entry")

    # #region agent log
    _debug_log = (Path(__file__).resolve().parent.parent.parent / ".cursor" / "debug.log")
    try:
        with open(_debug_log, "a", encoding="utf-8") as _f:
            _f.write(json.dumps({"location": "rag_service.py:rag_tool:entry", "message": "rag_tool entry", "data": {"query": query.strip()[:200], "thread_id": thread_id}, "timestamp": __import__("time").time() * 1000, "sessionId": "debug-session", "hypothesisId": "H1,H5"}) + "\n")
    except Exception:
        pass
    # #endregion

    # Extract user_id from thread_id for filtering
    user_id = _get_user_id_for_thread(thread_id) if thread_id else None
    _rag_step("resolve_user_id")
    logger.info(f"rag_tool: extracted user_id={user_id}")

    # Parse page requests from query
    import re
    page_patterns = [
        r'page\s+(?:no|number|#)?\s*(\d+)',
        r'page:\s*(\d+)',
        r'on\s+page\s+(\d+)',
        r'page\s+(\d+)',
    ]
    
    page_requested = None
    for pattern in page_patterns:
        match = re.search(pattern, query.lower())
        if match:
            try:
                page_requested = int(match.group(1))
                logger.info(f"rag_tool: detected page request: {page_requested}")
                # Call get_page_tool instead of similarity search
                if thread_id:
                    _rag_step("page_request_detected")
                    out = get_page_tool.invoke({"page": page_requested, "thread_id": thread_id})
                    _write_speed_log("rag_tool", thread_id, rag_steps, rag_started)
                    return out
                else:
                    return "Error: thread_id is required for page queries."
            except (ValueError, IndexError):
                pass
    _rag_step("page_request_parsed")

    # Check for author/title/person-identity queries (e.g. "who is X?", "who wrote?", "author")
    author_keywords = ["author", "written by", "who wrote", "title page", "lecturer", "who is the author"]
    is_author_query = any(keyword in query.lower() for keyword in author_keywords)
    # "who is <name>?" should get consistent results: always include page 1 (resumes/PDFs often put name there)
    is_who_is_person = query.strip().lower().startswith("who is ") and len(query.strip()) > 10
    is_person_identity_query = is_author_query or is_who_is_person

    # Get retriever for similarity search (pass rag_steps for per-step timing inside retriever)
    retriever = _get_retriever(thread_id, user_id, steps_list=rag_steps)
    _rag_step("get_retriever")
    if retriever is None:
        # If person/author query and no retriever, try page 1 fallback
        if is_person_identity_query and thread_id:
            logger.info("rag_tool: person/author query with no retriever, trying page 1 fallback")
            out = get_page_tool.invoke({"page": 1, "thread_id": thread_id})
            _write_speed_log("rag_tool", thread_id, rag_steps, rag_started)
            return out
        _write_speed_log("rag_tool", thread_id, rag_steps, rag_started)
        return "Error: No document indexed for this chat. Upload a PDF first."

    # Perform similarity search
    result = retriever.invoke(query)
    _rag_step("similarity_search")
    logger.info(f"rag_tool: similarity search returned {len(result)} documents after filtering")
    
    context = [doc.page_content for doc in result]
    metadata = [doc.metadata for doc in result]
    
    # For "who is X?" / author queries: always include page 1 so the answer is consistent (name often on page 1)
    if is_person_identity_query and thread_id:
        page1_result = get_page_tool.invoke({"page": 1, "thread_id": thread_id})
        if isinstance(page1_result, dict) and "content" in page1_result:
            page1_content = page1_result.get("content", [])
            if page1_content:
                # Prepend page 1 so the model sees it first; avoid duplicating if already in context
                existing_text = "\n".join(context).lower()
                new_chunks = [c for c in page1_content if c and c.strip() and c.strip().lower() not in existing_text]
                if new_chunks:
                    context = list(new_chunks) + context
                    metadata = list(page1_result.get("metadata", [])[: len(new_chunks)]) + metadata
                    logger.info(f"rag_tool: prepended page 1 for person/author query ({len(new_chunks)} chunks)")
                elif not context:
                    context = list(page1_content)
                    metadata = list(page1_result.get("metadata", [])[: len(page1_content)])
                    logger.info(f"rag_tool: used page 1 only for person/author query ({len(context)} chunks)")
    # Legacy fallback: author query with no/inadequate results still get page 1 (already handled above)
    
    # Get thread metadata from DB
    thread_meta = _get_thread_metadata_from_db(str(thread_id)) or {}
    _rag_step("load_thread_metadata")
    num_pages = thread_meta.get("num_pages") or thread_meta.get("pages") or thread_meta.get("documents")
    source_file = thread_meta.get("filename") or "PDF"

    # Return content-only string for the LLM so internal metadata is not shown to the user
    cleaned_chunks = [_strip_metadata_like_lines(c) for c in context if c and c.strip()]
    _rag_step("clean_chunks")
    content_block = "\n\n---\n\n".join(cleaned_chunks) if cleaned_chunks else "(No relevant content found.)"
    content_for_llm = (
        f"Relevant content from the PDF:\n\n{content_block}\n\n"
        f"Source: {source_file}. Total pages: {num_pages or 'unknown'}."
    )
    _rag_step("build_content_for_llm")

    # #region agent log
    try:
        with open(_debug_log, "a", encoding="utf-8") as _f:
            _f.write(json.dumps({"location": "rag_service.py:rag_tool:exit", "message": "rag_tool exit", "data": {"query": query.strip()[:120], "result_count": len(result), "chunks_returned": len(cleaned_chunks), "content_length": len(content_for_llm), "content_has_hamza": "hamza" in content_block.lower()}, "timestamp": __import__("time").time() * 1000, "sessionId": "debug-session", "hypothesisId": "H2,H3,H4"}) + "\n")
    except Exception:
        pass
    # #endregion

    _write_speed_log("rag_tool", thread_id, rag_steps, rag_started)
    return content_for_llm




tools = [calculator, rag_tool, get_page_tool, list_topics_whole_doc_tool,count_pdf_words_tool,count_words_in_text_tool]
# Note: llm_with_tools and llm_structured_output are now created per-request in chat_node
# to use user-specific API keys and provider settings

# -------------------
# 5. State
# -------------------


class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    lesson_in_progress: bool
    lesson_finalized: bool
    last_lesson_text: str

class LessonState(TypedDict):
    lesson_in_progress: bool
    lesson_finalized: bool
    last_lesson_text: str
    lesson_title: str


class IsLessonCheck(BaseModel):
    """Structured output for LLM check: is the given content a lesson to finalize?"""

    is_lesson: bool = Field(
        description="True if the content is a complete or substantive lesson (educational material with structure, headings, etc.). False if it is a short reply, clarification, or non-lesson content."
    )


# Prompt for lesson validation: is the given content a lesson worth finalizing?
# Uses both user query and AI response: only finalize when user asked to create a lesson AND the AI produced one.
LESSON_VALIDATION_PROMPT = """You are a validator. Your task is to determine if the AI response below is a LESSON that the user explicitly asked to create (and should be finalized/saved).

Consider BOTH:

1) USER'S QUERY (the message that preceded the AI response):
---
{user_query}
---

2) AI'S RESPONSE:
---
{content}
---

A LESSON TO FINALIZE means:
- The user explicitly requested lesson creation (e.g. "create a lesson", "generate a lesson", "make a lesson plan", "create lesson on X")
- The AI response is the generated lesson: educational, structured (headings, sections), substantive

NOT a lesson to finalize:
- User asked a question (e.g. "what is X?", "explain Y") and AI gave an educational answer — that is Q&A, not a lesson
- User said "finalize" but the AI response above is a short reply, clarification, or non-lesson content
- Greetings, thanks, casual chat, meta-discussion

Output your judgment: user must say somethin like "create a lesson", "generate a lesson", "make a lesson plan", "create lesson on X" to finalize the lesson. Then in  the  AI RESPINSE must be a well structured lesson with headings, sections, bullet points, or organized topics."""


# -------------------
# 6. Nodes
# -------------------


def chat_node(state: ChatState, config=None):
    """LLM node that may answer or request a tool call."""
    perf_steps = []
    perf_started = time.perf_counter()

    def _mark_step(label: str) -> None:
        perf_steps.append((label, time.perf_counter()))

    thread_id = None
    thread_id_str = None
    if config and isinstance(config, dict):
        thread_id = config.get("configurable", {}).get("thread_id")
        if thread_id:
            thread_id_str = str(thread_id)
    _mark_step("resolve_thread_id")

    # Check if a PDF document exists for this thread (from DB)
    has_document = False
    if thread_id_str:
        has_document = thread_has_document(thread_id_str)
    _mark_step("check_thread_document")

    # Get user_id from thread_id
    user_id = _get_user_id_for_thread(thread_id_str) if thread_id_str else None
    _mark_step("resolve_user_id")
    
    # Get active provider from admin settings (needed for error handling and rate limiting)
    # Initialize with default first to ensure it's always defined
    provider = os.getenv('LLM_PROVIDER', 'groq').lower()
    try:
        from app.utils.db import get_db
        from app.models.database_models import SystemSettings
        db = get_db()
        # Check for new active_provider setting first
        setting = db.query(SystemSettings).filter(SystemSettings.key == 'active_provider').first()
        if setting:
            provider = setting.value.lower()
        else:
            # Fallback to old llm_provider setting
            setting = db.query(SystemSettings).filter(SystemSettings.key == 'llm_provider').first()
            if setting:
                provider = setting.value.lower()
    except Exception as e:
        logger.warning(f"Error getting provider from settings: {str(e)}, using default: {provider}")
    _mark_step("load_provider_settings")

    # Use new get_chat_model which handles admin/user settings automatically
    logger.info(f"Creating LLM for user {user_id} (thread: {thread_id_str}, provider: {provider})")
    
    try:
        # Use cached LLM instance to avoid recreating on every call
        if user_id:
            # Include provider in cache key to ensure correct provider is used
            cache_key = f"{user_id}_{provider}_factory"
            with _llm_cache_lock:
                if cache_key not in _llm_cache:
                    logger.debug(f"Creating new LLM instance using get_chat_model for user {user_id} with provider {provider}")
                    _llm_cache[cache_key] = get_chat_model(user_id=user_id, timeout=120, temperature=0.7)
                    logger.info(f"Created and cached {provider} LLM instance for user {user_id}")
                else:
                    logger.debug(f"Reusing cached LLM instance for user {user_id} with provider {provider}")
                user_llm = _llm_cache[cache_key]
        else:
            # No user_id, use fallback
            user_llm = get_rag_llm(user_id=None, provider=provider, timeout=120, temperature=0.7)
        
        user_llm_with_tools = user_llm.bind_tools(tools)
        user_llm_structured_output = user_llm.with_structured_output(LessonState)
        logger.debug(f"Successfully created/retrieved {provider} LLM instance for user {user_id}")
        _mark_step("init_llm")
    except Exception as e:
        logger.error(f"Error creating user-specific LLM: {str(e)}, falling back to global LLM")
        # Fallback to global LLM if user-specific LLM creation fails
        # But only if it's not a missing API key error
        if "API key" in str(e) or "api key" in str(e).lower():
            _write_speed_log("chat_node", thread_id_str, perf_steps, perf_started)
            error_response = AIMessage(
                content=(
                    f"⚠️ **API Key Error**: {str(e)}\n\n"
                    f"Please configure your {provider.upper()} API key to continue using the chat feature."
                )
            )
            return {"messages": [error_response]}
        
        # Fallback to generic LLM
        user_llm = get_rag_llm(user_id=None, provider=provider, timeout=120, temperature=0.7)
        user_llm_with_tools = user_llm.bind_tools(tools)
        user_llm_structured_output = user_llm.with_structured_output(LessonState)
        _mark_step("init_llm_fallback")

    # Get custom prompt from database (user-level, applies to all threads)
    custom_prompt = _get_rag_prompt(user_id, thread_id_str)
    # custom_prompt = """
    # Section A
    # # Prof. Potter - Lesson Planning Assistant
    # 1.	You are Prof. Potter, an expert education assistant helping Faculty/Teachers prepare lesson plans from uploaded documents.
    # **Communication Style**
    # 2.	Greeting (first interaction only): "Hello, I'm Prof. Potter, here to help you prepare your lesson plan." (≤20 words)
    # **CRITICAL INSTRUCTION 1: Dual-Verification Before Response**
    # 4.	For every Faculty question, follow this exact process:
    # 4.1.	Reread the original question the Faculty asked and ask for any clarification, engage in the conversation, and exchange after the teacher has made it clear as to what he/she are looking for
    # 4.2.	Generate two independent answers to the teacher's question internally
    # 4.3.	Compare both answers and only when answers match ≥98%, provide the answer to the Faculty
    # 4.4.	If internal answers don't match ≥98%, this signals ambiguity - return to instruction 4.
    # 4.5.	This verification happens silently - Faculty does not see this process
    # 5.	Remove any repetitive sentences within the response (unless repetition serves to reinforce learning)

    # **CRITICAL INSTRUCTION 2: Ambiguity Resolution Process**
    # 6.	When a question can be interpreted in multiple ways, STOP immediately and ask additional clarifying questions
    # 7.	Always build the lesson logically from the prerequisites to the main topic
    #  
    # Section B: The method
    # 1.	The teacher proceeds to ask LLM a question, and LLM uses the following process, without revealing until step N: 
    # Given the teacher's request for help in preparing a class lesson, the LLM first identifies the subject, then the topic, and finally the concept to be explained in the lesson. 
    # 1.1.	Ask the teacher for confirmation.
    # 1.1.1.	If confirmed by the teacher, then LLM continues.
    # 1.1.2.	Otherwise, ask the teacher for clarification
    # 2.	Continuing, the LLM identifies the corresponding mathematical equation associated with the lesson plan’s content. (This is the first critical path to teaching, connecting the concept with the mathematical equation.)
    # DISSECTING EQUATIONS
    # 2.1.	LLM identifies and explains all the terms in the equations.
    # 2.2.	LLM explains the PHYSICAL meaning of each term in the equation
    # 3.	LLM explains that equations involve an equal sign, where one side of the equation is equal to the other side. Another way of saying the same thing is that the term on one side of the equal sign balances the terms on the other side of the equal sign.
    # 4.	Breaking down the equation, LLM explains that when looking at terms individually, one side of the term is proportional to the term on the other side of the equation.
    # Significance of the Terms Location in the Complete Equation
    # 5.	LLM explains the significance of the position of these terms, for example, whether they are in the numerator or the denominator.
    # 6.	Mathematical operations on Equation’s terms.
    # 6.1.	LLM so far has explained what the individual term means by itself
    # 6.2.	Now, LLM explains what the following mathematical operators do to the terms and then explains what the resulting terms mean physically
    # 6.2.1.	Exponents (positive or negative powers)
    # 6.2.2.	Square roots (√) and cube roots and more
    # 6.2.3.	Squared terms (²), Cubed terms, and more
    # 6.2.4.	Multiplied terms with exponents
    # 6.2.5.	Coefficients and their meaning
    # 6.3.	What the operator acting on the term produces weather physically or conceptually, meaning, what does it mean when the term is either squared, multiplied by a coefficient, multiplied by an exponent, and more
    # 6.4.	Explain the significance of each term's position in the equation (numerator vs denominator, exponents, powers, coefficients)
    # 7.	Narrate the equation as follows: verbally in a manner easily explainable at the student's grade level. 
    # 7.1.	Here we assume there is one term on the left side of the equal sign, and on the other side of the equal sign, there are two terms multiplied by each other and another term in the denominator that is squared. The term on the right side is multiplied by another term. This is how LLM will explain the equation
    # 7.2.	The left side term is proportional to the right side’s first term
    # 7.3.	The left side term is proportional to the right side’s second term
    # 7.4.	The left side term is inversely proportional to the right side of the equation; it is inversely proportional since it is in the denominator.
    # 7.5.	Important point is the term in the denominator is squared, so it decreases the value on the left side of the equation by a square, meaning if the denominator term doubles, the term on left side decreases by fourth, and if the denominator term increases by a cube and is squared, the term on the left side will decrease by 9 times.
    # 7.6.	After LLM explained that the combination of all terms on the right side is proportional to the term on the left side, the proportional sign is now replaced with an equal sign and a constant. 
    # 7.6.1.	Explain to students that when a proportionality is removed and replaced by an equal sign, it also adds a constant. This is the complete equation.
    # 8.	Real world example
    # 8.1.	Newton Gravitational Law; 
    # 8.2.	Hydrostatic Pressure
    # 8.3.	Equation of continuity in fluids, LLM adopts the following process and explains lessons from simpler to more detailed
    # 8.3.1.	Explain by saying the cross-sectional area where fluid is passing through with velocity is a constant. 
    # 8.3.2.	The cross-sectional area size of a pipe multiplied by the velocity of the same fluid passing through the same size cross-section is equal to a different cross-sectional area size and multiplied by a different velocity. 
    # 8.3.3.	Furthermore, it means cross-sectional area 1, which has a liquid passing through, is multiplied by the same liquid's velocity 1, and that is EQUAL to different cross-sectional area 2 multiplied by different velocity 2.  
    # 8.3.4.	Giving a real-world example: Imagine a long hose, and water is passing through. At one point in the long pipe, the pipe is squeezed, and by the action of squeezing, the cross-section of the pipe is reduced. What the equation of continuity states is that two quantities, that is, cross-sectional area multiplied by velocity of the liquid passing through the same cross-sectional area, must remain a constant value. Meaning, imagine the constant here is 16, so the equation states that when you multiply the two quantities, it must always be equal to 16. The two quantities multiplied here are cross-sectional area and velocity; when multiplied, they need to produce a result of 16. For example, if one quantity is 8, the other must be 2. If one quantity is 4, the other must also be 4 to produce the same constant, 16. 
    # 8.3.5.	What does it mean physically? It means enlarging one quantity automatically reduces the other’s quantity, so if you reduce the cross-sectional area, the velocity needs to increase. Let's look at this with a real-life example, say you are holding the end of the garden hose where the water is flowing out. By squeezing the end of the garden hose with your hand, you immediately observe the water exiting the hose more rapidly. Stated differently, reducing the cross-sectional area at the end of the hose increases the water velocity exiting the hose.
    # 9.	This Section A:  The Method, happens silently – Faculty/Teacher does not see this process
    



    #  
    # Section C: The Lecture Generation Process
    # 1.	The following steps are in the lesson to be generated
    # 1.1.	State the subject being discussed
    # 1.2.	State the subject's context as to what is being talked about
    # 1.3.	State the verbal definition of the concept, clearly with heavy emphasis on using the correct definition and, within it, using the exact terminology, and before diving deep into the lesson. 
    # 1.4.	Understand the Faculty's lesson topic, and suggest the prerequisites students need
    # 1.4.1.	Clearly state: "For students to understand [topic], they need to know [prerequisites]. Would you like me to include prerequisite material in the lesson plan?"
    # 1.4.2.	If prerequisites are not in the document, inform the Faculty and ask: "How would you like me to address prerequisites not covered in this document?" Follow the instructions of the Faculty/Teacher
    # 1.4.3.	Go through Section B  
    # 1.5.	Most importantly, differentiate by comparing the current lesson from the previous lesson, and if not available, check the curriculum as to what was taught before the current lesson, and differentiate the two clearly by doing the following
    # 1.5.1.	Differentiate the subject of the current lesson from the previous lesson
    # 1.5.2.	Differentiate the context of the previous lesson from the current lesson
    # 1.5.3.	Describe and explain what the lesson is to be learnt here and compare with the previous lesson or previous subject in the curriculum
    # 1.6.	Differentiate each term involved in the current lesson from each term involved in the previous lesson
    # 1.7.	If one lesson has an equation while the other lesson is a concept, explain both, compare both, and differentiate both.
    # 2.	LLM narrate the complete equation verbally in a manner easily understandable at the student's grade level.
    # 3.	Build the lesson logically from the prerequisites to the main topic and the conclusion
    #  
    # Section D: Lesson Structure
    # Step 1: State formal definition → Section B

    # Step 2: List prerequisites → justify necessity → rank importance →  Section B

    # Step 3: Teach prerequisites (def + explanation + example) → justify universal coverage (strong/struggling/all benefits)

    # Step 4: Connect to prerequisites → differentiate explicitly (use exact pattern) → confront misconception (state/why wrong/correct/why develops) → Follow instructions in Section B

    # Step 5: Extract key terms → define each → identify CRITICAL term (essential because/missing causes/this means) → Follow instructions Section C 

    # Step 6: Create concrete scenario with numbers → work step-by-step → highlight distinction → show misconception fails → Follow instructions Section C

    # Step 7: Show concept interaction → give real applications → synthesize completely → Follow instructions Section B

    # Step 8: Ask assessment questions → address remaining confusion → confirm all objectives → Confer uploaded document
    # **instruction**
    # **very very important instruction***
    # #while creating te lecture each headings should be present in paragraph form and max of 9 to 10 lines explanation on each headings

    # Output: Complete explanation with all 8 Steps, all mandatory components, meeting all quality standards
        
        
        
    
    
    # """
    
    if has_document:
        # Get document info from DB
        doc_meta = _get_thread_metadata_from_db(str(thread_id)) or {}
        filename = doc_meta.get("filename", "PDF")
        num_pages = doc_meta.get("num_pages") or doc_meta.get("pages") or doc_meta.get("documents")
        page_info = f" The PDF has {num_pages} pages." if num_pages else ""
        
        # Default RAG instructions (always included)
        rag_instructions = (
    f"IMPORTANT: When the user asks ANY question that could be answered by the PDF, you MUST:\n"
    f"1. Call the rag_tool function\n"
    f"2. Pass the user's question as the 'query' parameter\n"
    f"3. Pass '{thread_id}' as the 'thread_id' parameter (this is REQUIRED)\n\n"

    f"CRITICAL: Page Number Queries:\n"
    f"- If the user asks about a specific page number (e.g., 'what is on page 0', 'page 1', 'page number 2'),\n"
    f"  you MUST call get_page_tool(page=<n>, thread_id='{thread_id}') instead of rag_tool.\n"
    f"- Page indexing: If user says 'page 0', treat it as the first page (page 1 in the PDF).\n"
    f"- The get_page_tool will return the exact content of that page reliably.\n"
    f"- Always use get_page_tool for page-specific queries to ensure accuracy.\n\n"

    f"CRITICAL: Topics/Outline/Chapters Queries:\n"
    f"- If the user asks for a list of topics, outline, chapters, headings, table of contents, or what the document covers,\n"
    f"  you MUST call list_topics_whole_doc_tool(thread_id='{thread_id}') FIRST. Do NOT answer from rag_tool for these queries.\n"
    f"- Examples that REQUIRE list_topics_whole_doc_tool (call it immediately):\n"
    f"  * 'what topic covered' / 'what topics are covered' / 'what topics does this document cover'\n"
    f"  * 'show me the list of topics' / 'what are the topics in this document'\n"
    f"  * 'list all chapters' / 'show me the outline' / 'table of contents'\n"
    f"  * 'headings' / 'sections' / 'what does the document cover'\n"
    f"- After calling the tool, present the full 'topics' list from the response to the user. Do not summarize to only a few items.\n\n"

    f"When generating a LECTURE or LESSON, you MUST follow these rules strictly:\n"
    f"- Use clear and meaningful headings\n"
    f"- Under EACH heading, write a DETAILED explanation in PARAGRAPH form\n"
    f"- Each paragraph should be 7 to 8 complete sentences\n"
    f"- DO NOT write one-line summaries under headings\n"
    f"- DO NOT use bullet points unless the user explicitly asks for them\n"
    f"- Write in an academic, lecture-style tone suitable for teaching\n"
    f"- Explain concepts clearly, as if teaching students\n\n"

    f"When you call rag_tool, it returns relevant PDF content only (no internal metadata).\n"
    f"Use that content to answer. Do not mention internal metadata, file paths, or technical notes to the user.\n\n"

    f"When you call get_page_tool, it will return:\n"
    f"- Exact content from the specified page\n"
    f"- Page number requested and resolved\n"
    f"- Number of chunks found on that page\n"
    f"- All content and metadata from that page\n\n"

    f"Always integrate PDF content naturally into explanations instead of copying verbatim.\n"
    f"When asked about number of pages, use ONLY the num_pages or pages field.\n"
    f"Always return the response in MARKDOWN format.\n\n"

    f"CRITICAL: Lesson Finalization Rules:\n"
    f"- ONLY set lesson_finalized = true when the user EXPLICITLY requests to finalize the lesson\n"
    f"- User must say things like: 'finalize', 'this is final', 'I am satisfied', 'complete the lesson', 'save the lesson', etc.\n"
    f"- DO NOT automatically finalize lessons - wait for explicit user confirmation\n"
    f"- When user explicitly requests finalization, you MUST:\n"
    f"  * Set lesson_finalized = true\n"
    f"  * Provide a meaningful and specific lesson_title\n"
    f"  * The lesson_title must clearly reflect the lecture topic\n"
    f"  * Example titles: 'AI-Based Scheduling Systems', 'Conversational SaaS Platforms'\n"
    f"  * DO NOT use generic titles like 'Lesson' or 'Lecture'\n"
    f"- The output should be more than 15 to 16 lines in each heading in lesson creation\n"
    f"- In each heading the minimum words should be 120 to 150\n\n"

    f"CRITICAL: Word Count Requests (LLM must decide scope):\n"
    f"- If the user asks about 'word count', 'how many words', or similar, you MUST determine the target scope.\n"
    f"- Possible targets:\n"
    f"  (A) Uploaded PDF/document (entire document or specific pages)\n"
    f"  (B) Last assistant-generated content (e.g., lecture, article, explanation)\n"
    f"  (C) Last user message\n"
    f"  (D) Whole conversation (recent messages)\n\n"

    f"- First, inspect the last assistant message:\n"
    f"  • If it is long-form educational content (lecture, article, tutorial, explanation), "
    f"    treat it as 'last lecture/content' rather than 'last message'.\n\n"

    f"- If the user does NOT clearly specify the target, you MUST ask ONE clarification question only:\n"
    f"  • If the last assistant output is long-form content:\n"
    f"    'Do you mean the word count of the uploaded PDF, the whole conversation, or the last lecture I generated?'\n"
    f"  • Otherwise:\n"
    f"    'Do you mean the word count of the uploaded PDF, the whole conversation, or just the last message?'\n\n"

    f"- Once the target is clear, follow these rules strictly:\n"
    f"  • If user says PDF/document/pages → call count_pdf_words_tool(thread_id='{thread_id}', page=..., start_page=..., end_page=...)\n"
    f"  • If user says 'your answer', 'this lecture', 'last lecture', 'this explanation' → "
    f"    call count_words_in_text_tool(text=<last assistant message>, label='last_assistant')\n"
    f"  • If user says 'my message' → call count_words_in_text_tool(text=<last user message>, label='last_user')\n"
    f"  • If user says 'whole conversation', 'chat so far' → "
    f"    call count_words_in_text_tool(text=<join recent chat messages>, label='conversation')\n"


     f"CRITICAL OVERRIDE — Word Count Intent Resolution:\n"
    f"- If the user explicitly mentions any of the following:\n"
    f"  * pdf\n"
    f"  * document\n"
    f"  * uploaded file\n"
    f"  * pages\n"
    f"  THEN the request is NOT ambiguous\n"
    f"- In this case:\n"
    f"  * DO NOT ask a clarification question\n"
    f"  * IMMEDIATELY call the PDF word count tool\n"
    f"  * NEVER guess or estimate the word count\n\n"

    f"IRRELEVANT QUESTIONS:\n"
    f"- If the user's question is NOT relevant to the uploaded PDF (e.g. unrelated topic, general knowledge, or not answerable from the document),\n"
    f"  you MUST respond with exactly this phrase and nothing else for the main message:\n"
    f"  \"Irrelevant question. Do you want me to answer from my own knowledge base?\"\n"
    f"- Do not mention any tool names (rag_tool, get_page_tool, etc.) in your response to the user.\n\n"

)

        # Combine custom prompt with default RAG instructions
        if custom_prompt:
            # Custom prompt + default RAG instructions
            base_content = (
                f"{custom_prompt}\n\n"
                f"---\n\n"
                f"You are a helpful assistant. A PDF document ({filename}) has been uploaded for this conversation.{page_info}\n\n"
                f"{rag_instructions}"
            )
        else:
            # Default system message with RAG instructions
            base_content = (
                f"You are a helpful assistant. A PDF document ({filename}) has been uploaded for this conversation.{page_info}\n\n"
                f"{rag_instructions}"
            )
        
        system_message = SystemMessage(content=base_content)
    else:
        # No document uploaded
        if custom_prompt:
            # Use custom prompt even when no document
            system_message = SystemMessage(content=custom_prompt)
        else:
            # Default message when no document
            system_message = SystemMessage(
                content=(
                    "You are a helpful assistant. No PDF document has been uploaded yet. "
                    "You can use web search, stock price, and calculator tools when helpful. "
                    "If the user asks about a PDF, ask them to upload one first."
                )
            )

    # Progressive message reduction on token errors
    conversation_messages = state["messages"]
    initial_max_messages = 7  # Start with 7 messages
    max_attempts = 4  # Try with 7, 5, 3, 1 messages
    
    def _is_token_error(error_msg: str) -> bool:
        """Check if error is related to token/context length limits."""
        error_lower = error_msg.lower()
        token_keywords = [
            "maximum context length",
            "context length exceeded",
            "exceeds maximum",
            "too many tokens",
            "maximum tokens",
            "context window",
            "token limit",
            "token count",
            "input length",
            "maximum input length",
            "input tokens",
            "tokens per minute",  # Groq TPM limit
            "tpm",  # Tokens per minute abbreviation
            "request too large",  # 413 Payload Too Large
            "payload too large",  # 413 error
        ]
        # Also check for 413 status code
        is_413 = '413' in error_msg or 'payload too large' in error_lower
        return is_413 or any(keyword in error_lower for keyword in token_keywords)
    
    def _prepare_messages(num_messages: int):
        """Prepare messages list with specified number of conversation messages.
        Ensures tool messages are always properly paired with their assistant messages.
        Only includes complete tool call sequences (assistant with tool_calls + all corresponding tool messages)."""
        if len(conversation_messages) <= num_messages:
            return [system_message, *conversation_messages]
        
        # Helper function to check if a message is an assistant with tool_calls
        def is_assistant_with_tool_calls(msg):
            return isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls
        
        # Helper function to check if a message is a tool message
        def is_tool_message(msg):
            return isinstance(msg, ToolMessage)
        
        # Helper function to find complete tool call sequence starting from an assistant message
        def get_tool_sequence_start(assistant_idx):
            """Returns the start index of a complete tool call sequence, or None if incomplete."""
            if assistant_idx < 0 or assistant_idx >= len(conversation_messages):
                return None
            
            assistant_msg = conversation_messages[assistant_idx]
            if not is_assistant_with_tool_calls(assistant_msg):
                return None
            
            # Get all tool_call_ids from the assistant message
            tool_call_ids = {tc.get('id') for tc in assistant_msg.tool_calls if isinstance(tc, dict) and 'id' in tc}
            if not tool_call_ids:
                return None
            
            # Look forward to find all corresponding tool messages
            found_tool_ids = set()
            tool_start_idx = assistant_idx + 1
            
            # Collect all consecutive tool messages
            for j in range(assistant_idx + 1, len(conversation_messages)):
                msg = conversation_messages[j]
                if is_tool_message(msg):
                    tool_id = getattr(msg, 'tool_call_id', None)
                    if tool_id and tool_id in tool_call_ids:
                        found_tool_ids.add(tool_id)
                else:
                    # Stop at first non-tool message
                    break
            
            # Only return if we found all tool responses
            if found_tool_ids == tool_call_ids:
                return assistant_idx
            return None
        
        # Start from the end and work backwards, including complete sequences only
        limited_messages = []
        included_indices = set()
        i = len(conversation_messages) - 1
        
        while i >= 0 and len(limited_messages) < num_messages:
            if i in included_indices:
                i -= 1
                continue
            
            msg = conversation_messages[i]
            
            # If this is a tool message, skip it - we'll handle it when we encounter its assistant
            if is_tool_message(msg):
                i -= 1
                continue
            elif is_assistant_with_tool_calls(msg):
                # Check if this is a complete sequence
                seq_start = get_tool_sequence_start(i)
                if seq_start == i:
                    # Complete sequence, include it
                    tool_msgs = []
                    for k in range(i + 1, len(conversation_messages)):
                        if k in included_indices:
                            break
                        next_msg = conversation_messages[k]
                        if is_tool_message(next_msg):
                            tool_msgs.append((k, next_msg))
                        else:
                            break
                    
                    sequence_size = 1 + len(tool_msgs)
                    if len(limited_messages) + sequence_size <= num_messages:
                        # Add assistant message
                        limited_messages.insert(0, msg)
                        included_indices.add(i)
                        # Add tool messages right after assistant (in order)
                        for idx, (tool_idx, tool_msg) in enumerate(tool_msgs):
                            limited_messages.insert(1 + idx, tool_msg)
                            included_indices.add(tool_idx)
                    i -= 1
                else:
                    # Incomplete sequence, skip it
                    i -= 1
            else:
                # Regular message (user, system, etc.), include it
                if len(limited_messages) < num_messages:
                    limited_messages.insert(0, msg)
                    included_indices.add(i)
                i -= 1
        
        logger.debug(f"Limited conversation history to latest {len(limited_messages)} messages (requested {num_messages})")
        return [system_message, *limited_messages]
    
    # Try with progressively fewer messages if token errors occur
    # For Groq, reduce max attempts to avoid rate limit cascades (Groq SDK handles retries internally)
    effective_max_attempts = max_attempts if provider != 'groq' else min(max_attempts, 2)
    logger.debug(f"Using {effective_max_attempts} max attempts for provider {provider}")
    for attempt in range(effective_max_attempts):
        # Calculate number of messages for this attempt: 7, 5, 3, 1
        if attempt == 0:
            current_max = initial_max_messages
        elif attempt == 1:
            current_max = 5
        elif attempt == 2:
            current_max = 3
        else:
            current_max = 1
        
        messages = _prepare_messages(current_max)
        
        try:
            # Make sequential calls to avoid rate limits
            # Use global rate limiter for Groq
            if provider == 'groq':
                groq_rate_limiter.wait_if_needed()
            
            # First get the main response
            response = user_llm_with_tools.invoke(messages, config=config)
            _mark_step("llm_invoke")

            # Record success to reset error count
            if provider == 'groq':
                groq_rate_limiter.record_success()

            # Extract lesson text from AI response
            response_content = response.content if hasattr(response, 'content') else str(response)
            _mark_step("extract_response")
            
            # Try to get lesson state, but make it optional to save tokens and avoid rate limits
            # Skip lesson_state call for Groq to reduce API calls and avoid rate limits

            last_user_msg_text = ""
            try:
                from langchain_core.messages import HumanMessage
                for msg in reversed(conversation_messages):
                    if isinstance(msg, HumanMessage):
                        last_user_msg_text = (msg.content or "")
                        break
            except Exception:
                last_user_msg_text = ""

            msg_lower = last_user_msg_text.lower()

            needs_lesson_state = any(k in msg_lower for k in [
                "lesson", "lecture", "lesson plan", "generate a lesson", "create a lesson",
                "finalize", "finalise", "save the lesson", "complete the lesson",
                "lesson title", "make this final"
            ])

            lesson_state = None

            # Only make the second call when needed
            if provider != "groq" and needs_lesson_state:
                try:
                    # time.sleep(0.5)  # optional
                    lesson_state = user_llm_structured_output.invoke(messages, config=config)
                    _mark_step("lesson_state_invoke")
                except Exception as lesson_error:
                    logger.warning(f"Failed to get lesson state (non-critical): {str(lesson_error)}")
                    lesson_state = {
                        "lesson_in_progress": False,
                        "lesson_finalized": False,
                        "last_lesson_text": response_content,
                        "lesson_title": ""
                    }
            else:
                # No second call: still keep a consistent structure for downstream logic
                lesson_state = {
                    "lesson_in_progress": False,
                    "lesson_finalized": False,
                    "last_lesson_text": response_content,
                    "lesson_title": ""
                }
            # lesson_state = None
            # if provider != 'groq':
            #     # Only call lesson_state for non-Groq providers to avoid rate limits
            #     try:
            #         # Add delay before the second call to avoid rate limits
            #         # time.sleep(0.5)  # 500ms for other providers
            #         lesson_state = user_llm_structured_output.invoke(messages, config=config)
            #     except Exception as lesson_error:
            #         logger.warning(f"Failed to get lesson state (non-critical): {str(lesson_error)}")
            #         lesson_state = {
            #             "lesson_in_progress": False,
            #             "lesson_finalized": False,
            #             "last_lesson_text": response_content,  # Fallback: use response content
            #             "lesson_title": ""
            #         }
            # else:
            #     # For Groq, skip lesson_state to avoid rate limits and save tokens
            #     logger.debug("Skipping lesson_state call for Groq to avoid rate limits")
            #     lesson_state = {
            #         "lesson_in_progress": False,
            #         "lesson_finalized": False,
            #         "last_lesson_text": response_content,
            #         "lesson_title": ""
            #     }
          
            # lesson_state is a dict (TypedDict), so access it with dictionary syntax
            # Only finalize lesson if user explicitly requests it
            # Check the last user message for explicit finalization requests
            user_wants_to_finalize = False
            if conversation_messages:
                # Get the last user message
                last_user_msg = None
                for msg in reversed(conversation_messages):
                    from langchain_core.messages import HumanMessage
                    if isinstance(msg, HumanMessage):
                        last_user_msg = msg.content.lower() if hasattr(msg, 'content') else str(msg).lower()
                        break
                
                # Static approach: check for finalization keywords in user message
                # IMPORTANT: Do NOT treat generic creation phrases like "create the lesson"
                # as finalization triggers, otherwise normal lesson-creation requests will
                # immediately mark the lesson as finalized and show a misleading message.
                if last_user_msg:
                    finalization_keywords = [
                        # direct "final" / "done" intents
                        'finalize', 'finalise', 'finalize the lesson', 'finalise the lesson',
                        'final', 'this is final', 'this is the final',
                        'lesson final', 'lesson finalized',
                        'i am satisfied', "i'm satisfied", 'i am done', "i'm done",
                        'complete the lesson', 'finish the lesson', 'save the lesson',
                        'this lesson is complete', 'lesson is ready', 'ready to save',
                        'finalize this lesson', 'finalise this lesson', 'make this final'
                    ]
                    user_wants_to_finalize = any(keyword in last_user_msg for keyword in finalization_keywords)
            
            # Static approach: when user says final/finalized/create the lesson etc., validate then save the previous AI response (response -1)
            if thread_id_str and response_content and user_wants_to_finalize:
                finalized_source_content = response_content
                if conversation_messages:
                    try:
                        for msg in reversed(conversation_messages):
                            if isinstance(msg, AIMessage):
                                prev_text = (msg.content or "").strip()
                                if prev_text:
                                    finalized_source_content = prev_text
                                    break
                    except Exception:
                        pass
                if finalized_source_content:
                    # Get the user query that preceded the lesson (the message before the AI response we're finalizing)
                    user_query_before_lesson = ""
                    last_human_content = ""
                    for msg in conversation_messages:
                        if isinstance(msg, HumanMessage):
                            last_human_content = (msg.content or "").strip() if hasattr(msg, "content") else str(msg)
                        elif isinstance(msg, AIMessage):
                            ai_text = (msg.content or "").strip()
                            if ai_text and ai_text == finalized_source_content:
                                user_query_before_lesson = last_human_content
                                break
                    is_lesson = _check_if_content_is_lesson(
                        finalized_source_content,
                        user_query=user_query_before_lesson,
                        user_id=user_id,
                    )
                    if is_lesson:
                        _persist_finalized_lesson_static(thread_id_str, finalized_source_content)
                        _mark_step("persist_finalized_lesson")
                        try:
                            response.content = "Lesson finalized and saved. You can download it now."
                        except Exception:
                            pass
                    else:
                        _mark_step("lesson_validation_no")
                        try:
                            response.content = "There is no lesson to finalize."
                        except Exception:
                            pass
            else:
                # Track last AI response as "in-progress" lesson only when thread is NOT already finalized (so we don't overwrite the saved lesson)
                if thread_id_str and response_content:
                    try:
                        db = get_db()
                        thread_row = db.query(RAGThread).filter_by(thread_id=thread_id_str).first()
                        if thread_row and not getattr(thread_row, "lesson_finalized", False):
                            is_likely_lesson = (
                                len(response_content) > 200 or
                                '#' in response_content or
                                '\n\n' in response_content
                            )
                            if is_likely_lesson:
                                thread_row.last_lesson_text = response_content
                            db.commit()
                    except Exception as e:
                        logger.warning("Error saving lesson text to DB: %s", e)
                _mark_step("persist_in_progress_lesson")

            # Log if we had to reduce messages
            if attempt > 0:
                logger.info(f"Successfully processed request after reducing to {current_max} messages (attempt {attempt + 1})")
            _mark_step("postprocess_done")
            _write_speed_log("chat_node", thread_id_str, perf_steps, perf_started)
            
            return {"messages": [response]}
            
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            logger.warning(f"LLM API error in chat_node (attempt {attempt + 1} with {current_max} messages): {error_type}: {error_msg}")
            
            # Check for timeout exceptions by type (in addition to string matching)
            is_timeout_exception = (
                'Timeout' in error_type or 
                'TimeoutError' in error_type or
                hasattr(e, '__class__') and 'timeout' in e.__class__.__name__.lower()
            )
            
            # Record 429/413 errors for rate limiter adjustment
            # Note: 413 Payload Too Large is also a rate limit (TPM - tokens per minute)
            is_rate_limit_error = (
                '429' in error_msg or 
                '413' in error_msg or
                'Rate limit' in error_msg or 
                'rate_limit' in error_msg.lower() or
                'tokens per minute' in error_msg.lower() or
                'TPM' in error_msg
            )
            
            if provider == 'groq' and is_rate_limit_error:
                groq_rate_limiter.record_429_error()
                
                # Check if it's a token limit (TPM) error - these need message reduction
                is_token_limit = 'tokens per minute' in error_msg.lower() or 'TPM' in error_msg or '413' in error_msg
                
                if is_token_limit:
                    # For token limit errors, try with fewer messages if possible
                    if attempt < effective_max_attempts - 1:
                        logger.info(f"Groq token limit (TPM) error detected, retrying with fewer messages (attempt {attempt + 2})")
                        continue  # Retry with fewer messages
                    else:
                        # Last attempt failed, return error
                        logger.error(f"Groq token limit error after {effective_max_attempts} attempts.")
                        # Extract limit info from error if available
                        import re
                        limit_match = re.search(r'Limit (\d+)', error_msg)
                        requested_match = re.search(r'Requested (\d+)', error_msg)
                        limit = limit_match.group(1) if limit_match else '6000'
                        requested = requested_match.group(1) if requested_match else 'Unknown'
                        
                        error_response = AIMessage(
                            content=(
                                "⚠️ **Token Limit Exceeded**: Your request is too large for the current Groq plan.\n\n"
                                f"- **Limit**: {limit} tokens/minute\n"
                                f"- **Requested**: {requested} tokens\n\n"
                                "**Solutions:**\n"
                                "- Start a new conversation (shorter history)\n"
                                "- Reduce the conversation context\n"
                                "- Upgrade your Groq plan at https://console.groq.com/settings/billing\n\n"
                                f"*This error occurred after {effective_max_attempts} retry attempts.*"
                            )
                        )
                        _write_speed_log("chat_node", thread_id_str, perf_steps, perf_started)
                        return {"messages": [error_response]}
                else:
                    # For regular rate limit (429), don't retry in our loop - Groq SDK handles retries internally
                    # But we still need to return something to the user if it's the last attempt
                    logger.warning(f"Groq rate limit (429) error on attempt {attempt + 1}. Groq SDK will handle retry.")
                    if attempt >= effective_max_attempts - 1:
                        error_response = AIMessage(
                            content=(
                                "⚠️ **Rate Limit Reached**: Groq API rate limit has been exceeded.\n\n"
                                "The Groq service is currently handling too many requests. Please:\n"
                                "- Wait a few moments and try again\n"
                                "- Reduce the frequency of your requests\n"
                                "- Check your Groq API quota at https://console.groq.com\n\n"
                                f"*This error occurred after {effective_max_attempts} retry attempts.*"
                            )
                        )
                        _write_speed_log("chat_node", thread_id_str, perf_steps, perf_started)
                        return {"messages": [error_response]}
                    # For first attempts, continue to let Groq SDK handle retry
                    # But we need to wait a bit to avoid immediate retry
                    time.sleep(2)  # Wait 2 seconds before continuing
                    continue
            
            # Check if it's a Groq daily token limit error (429 with type 'tokens')
            if 'Rate limit reached' in error_msg and 'tokens per day' in error_msg and 'TPD' in error_msg:
                # Parse the error to extract useful information
                import re
                try:
                    # Extract limit, used, requested, and wait time
                    limit_match = re.search(r'Limit (\d+)', error_msg)
                    used_match = re.search(r'Used (\d+)', error_msg)
                    requested_match = re.search(r'Requested (\d+)', error_msg)
                    wait_match = re.search(r'try again in ([\dm\.]+)', error_msg)
                    
                    limit = limit_match.group(1) if limit_match else '100,000'
                    used = used_match.group(1) if used_match else 'Unknown'
                    requested = requested_match.group(1) if requested_match else 'Unknown'
                    wait_time = wait_match.group(1) if wait_match else 'Unknown'
                    
                    # Format numbers with commas
                    try:
                        limit = f"{int(limit):,}"
                        used = f"{int(used):,}"
                        requested = f"{int(requested):,}"
                    except:
                        pass
                    
                    error_response = AIMessage(
                        content=(
                            f"⚠️ **Groq Daily Token Limit Reached**\n\n"
                            f"You've reached your daily token limit for Groq API:\n"
                            f"- **Limit**: {limit} tokens/day\n"
                            f"- **Used**: {used} tokens\n"
                            f"- **Requested**: {requested} tokens\n"
                            f"- **Wait Time**: {wait_time}\n\n"
                            f"Please wait for the limit to reset, or upgrade your Groq plan at "
                            f"https://console.groq.com/settings/billing\n\n"
                            f"*The limit resets daily. You can continue using the service after the reset.*"
                        )
                    )
                    logger.error(f"Groq daily token limit reached: Used {used}/{limit}, Wait {wait_time}")
                    _write_speed_log("chat_node", thread_id_str, perf_steps, perf_started)
                    return {"messages": [error_response]}
                except Exception as parse_error:
                    logger.error(f"Error parsing Groq token limit error: {parse_error}")
                    # Fall through to generic error handling
            
            # Check if it's a timeout error (by exception type or message)
            is_timeout_error = is_timeout_exception or (
                "timeout" in error_msg.lower() or 
                "timed out" in error_msg.lower() or
                "Request timed out" in error_msg
            )
            
            if is_timeout_error:
                # For timeout errors, try once more with fewer messages if not last attempt
                if attempt < effective_max_attempts - 1:
                    logger.info(f"Timeout error detected, retrying with fewer messages (current: {current_max}, next: {current_max - 2 if current_max > 2 else 1})")
                    continue  # Retry with fewer messages
                else:
                    # Last attempt failed with timeout
                    logger.error(f"Request timed out after {effective_max_attempts} attempts. Final attempt with {current_max} messages.")
                    error_response = AIMessage(
                        content=(
                            "⚠️ **Request Timeout**: The request took too long to process.\n\n"
                            "This can happen when:\n"
                            "- The conversation history is very long\n"
                            "- The AI service is experiencing high load\n"
                            "- The network connection is slow\n\n"
                            "**Suggestions:**\n"
                            "- Try starting a new conversation\n"
                            "- Reduce the conversation history\n"
                            "- Try again in a few moments\n\n"
                            f"*The request timed out after multiple retry attempts.*"
                        )
                    )
                    _write_speed_log("chat_node", thread_id_str, perf_steps, perf_started)
                    return {"messages": [error_response]}
            
            # Check if it's a token error (context length)
            if _is_token_error(error_msg):
                # If this is not the last attempt, try with fewer messages
                if attempt < effective_max_attempts - 1:
                    logger.info(f"Token error detected, retrying with fewer messages (current: {current_max}, next: {current_max - 2 if current_max > 2 else 1})")
                    continue  # Retry with fewer messages
                else:
                    # Last attempt failed, show error
                    logger.error(f"All retry attempts failed with token errors. Final attempt with {current_max} messages.")
                    error_response = AIMessage(
                        content=(
                            "⚠️ **Context Length Error**: The conversation is too long to process. "
                            "Please start a new conversation or upload a shorter document.\n\n"
                            f"*Error details: {error_msg}*"
                        )
                    )
                    _write_speed_log("chat_node", thread_id_str, perf_steps, perf_started)
                    return {"messages": [error_response]}
            else:
                # Not a token error, check if it's a connection error
                if "Connection error" in error_msg or "No connection could be made" in error_msg or "actively refused" in error_msg:
                    error_response = AIMessage(
                        content=(
                            "⚠️ **Connection Error**: Unable to connect to the AI service. "
                            "The server may be temporarily unavailable.\n\n"
                            "Please try again in a few moments, or contact support if the issue persists.\n\n"
                            f"*Error details: {error_msg}*"
                        )
                    )
                else:
                    # Generic error handling (only show if not retrying)
                    if attempt < effective_max_attempts - 1:
                        # Try one more time with fewer messages even for non-token errors
                        logger.info(f"Non-token error detected, retrying with fewer messages (attempt {attempt + 2})")
                        continue
                    else:
                        # Final attempt failed
                        error_response = AIMessage(
                            content=(
                                "⚠️ **Error**: An error occurred while processing your request.\n\n"
                                "Please try again, or contact support if the issue persists.\n\n"
                                f"*Error details: {error_msg}*"
                            )
                        )
                
                _write_speed_log("chat_node", thread_id_str, perf_steps, perf_started)
                return {"messages": [error_response]}
    
    # Fallback: If we somehow exit the loop without returning, return a generic error
    # This should never happen, but ensures we always return a response
    logger.error(f"Retry loop completed without returning a response. This should not happen!")
    error_response = AIMessage(
        content=(
            "⚠️ **Error**: An unexpected error occurred while processing your request.\n\n"
            "Please try again, or contact support if the issue persists.\n\n"
            "*The request could not be completed after multiple retry attempts.*"
        )
    )
    _write_speed_log("chat_node", thread_id_str, perf_steps, perf_started)
    return {"messages": [error_response]}

tool_node = ToolNode(tools)

# -------------------
# 7. Checkpointer
# -------------------
conn = sqlite3.connect(database="chatbot.db", check_same_thread=False)
checkpointer = SqliteSaver(conn=conn)

# -------------------
# 8. Graph
# -------------------
graph = StateGraph(ChatState)
graph.add_node("chat_node", chat_node)
graph.add_node("tools", tool_node)

graph.add_edge(START, "chat_node")
graph.add_conditional_edges("chat_node", tools_condition)
graph.add_edge("tools", "chat_node")

chatbot = graph.compile(checkpointer=checkpointer)

# -------------------
# 9. Helpers
# -------------------
def retrieve_all_threads():
    all_threads = set()
    for checkpoint in checkpointer.list(None):
        all_threads.add(checkpoint.config["configurable"]["thread_id"])
    return list(all_threads)


def thread_has_document(thread_id: str) -> bool:
    """Check if thread has a document (from DB)."""
    try:
        db = get_db()
        thread = db.query(RAGThread).filter_by(thread_id=str(thread_id)).first()
        return thread is not None and getattr(thread, "has_document", False)
    except Exception as e:
        logger.warning("Error checking thread_has_document: %s", e)
        return False


def thread_document_metadata(thread_id: str) -> dict:
    """Get document metadata for a thread (from DB)."""
    meta = _get_thread_metadata_from_db(str(thread_id))
    return meta if meta else {}


def get_finalized_lesson(thread_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the last finalized lesson for a thread (from DB).
    Returns dict with last_lesson_text, lesson_title, lesson_finalized, or None if thread not found.
    """
    meta = _get_thread_metadata_from_db(str(thread_id))
    if not meta:
        return None
    text = (meta.get("last_lesson_text") or "").strip()
    return {
        "last_lesson_text": text,
        "lesson_title": (meta.get("lesson_title") or "").strip(),
        "lesson_finalized": meta.get("lesson_finalized", False),
    }


def _check_if_content_is_lesson(
    content: str, user_query: str = "", user_id: Optional[int] = None
) -> bool:
    """
    Use Grok LLM to determine if the given content is a lesson suitable for finalization.
    Requires both the user's query (that preceded the AI response) and the AI response.
    Returns True if user asked to create a lesson and the AI produced one, False otherwise.
    On error, returns False to avoid persisting non-lesson content.
    """
    if not (content or "").strip():
        return False
    try:
        llm = get_chat_model(user_id=user_id, timeout=60, temperature=0)
        llm_structured = llm.with_structured_output(IsLessonCheck)
        # Truncate very long content to avoid token limits (keep ~8k chars)
        content_sample = (content or "").strip()
        if len(content_sample) > 8000:
            content_sample = content_sample[:8000] + "\n\n[...content truncated for validation...]"
        user_query_sample = (user_query or "").strip() or "(no user query provided)"
        prompt = LESSON_VALIDATION_PROMPT.format(user_query=user_query_sample, content=content_sample)
        print("[Lesson Validation] INPUT user_query:", repr(user_query_sample[:500]))
        print("[Lesson Validation] INPUT content (first 500 chars):", repr(content_sample[:500]))
        result = llm_structured.invoke(prompt)
        is_lesson = result.is_lesson if hasattr(result, "is_lesson") else False
        print("[Lesson Validation] OUTPUT is_lesson:", is_lesson)
        return is_lesson
    except Exception as e:
        logger.warning("Lesson validation check failed: %s", e)
        return False


def _parse_lesson_title_from_content(content: str) -> str:
    """Extract Lesson Title from AI response text if present."""
    if not content:
        return ""
    m = re.search(r"Lesson\s+Title\s*:\s*[\"']([^\"']+)[\"']", content, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"Lesson\s+Title\s*:\s*(.+?)(?:\n|$)", content, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _persist_finalized_lesson_static(thread_id_str: str, response_content: str) -> None:
    """
    Static approach: save the current AI response as the finalized lesson to the RAG thread.
    Call this when the user's message contains finalization keywords (final, finalized, create the lesson, etc.).
    Optionally parses Lesson Title from response content.
    """
    if not thread_id_str or not (response_content or "").strip():
        return
    title = _parse_lesson_title_from_content(response_content)
    try:
        db = get_db()
        thread_row = db.query(RAGThread).filter_by(thread_id=thread_id_str).first()
        if thread_row:
            thread_row.lesson_finalized = True
            thread_row.last_lesson_text = response_content
            thread_row.lesson_title = title
            db.commit()
            logger.info(
                "Persisted finalized lesson (static, thread_id=%s, title=%s)",
                thread_id_str,
                (title[:50] + "…") if len(title) > 50 else title or "(none)",
            )
    except Exception as e:
        logger.warning("Error persisting finalized lesson: %s", e)


def _try_persist_finalized_from_response_content(thread_id_str: str, response_content: str) -> None:
    """
    Parse AI response for "Lesson Finalized: true" and "Lesson Title: ..." and persist to RAG thread.
    Used when structured lesson_state is not available (e.g. Groq). Kept for backward compatibility.
    """
    if not thread_id_str or not (response_content or "").strip():
        return
    content = response_content.strip()
    if not re.search(r"Lesson\s+Finalized\s*:\s*true", content, re.IGNORECASE):
        return
    # Reuse static persist (same DB update, title parsed inside)
    _persist_finalized_lesson_static(thread_id_str, response_content)


def update_lesson_finalized_status(thread_id: str, finalized: bool) -> bool:
    """
    Update the lesson finalized status for a thread (in DB).
    """
    try:
        db = get_db()
        thread = db.query(RAGThread).filter_by(thread_id=str(thread_id)).first()
        if not thread:
            return False
        thread.lesson_finalized = finalized
        db.commit()
        return True
    except Exception as e:
        logger.warning("Error updating lesson finalized: %s", e)
        return False


def save_finalized_lesson(thread_id: str, last_lesson_text: str, lesson_title: str = "") -> bool:
    """
    Save finalized lesson content and title to a thread (sets lesson_finalized=True).
    Used when the frontend sends the displayed finalized content (e.g. when backend did not persist).
    """
    try:
        db = get_db()
        thread = db.query(RAGThread).filter_by(thread_id=str(thread_id)).first()
        if not thread:
            return False
        thread.lesson_finalized = True
        thread.last_lesson_text = (last_lesson_text or "").strip() or thread.last_lesson_text
        thread.lesson_title = (lesson_title or "").strip()
        db.commit()
        return True
    except Exception as e:
        logger.warning("Error saving finalized lesson: %s", e)
        return False


def delete_thread(thread_id: str) -> dict:
    """
    Delete a thread: remove vectors from Milvus and optionally clean uploaded files.
    Note: RAGThread DB row is deleted by the route, not here.
    """
    thread_id_str = str(thread_id)
    user_id = _get_user_id_for_thread(thread_id_str)

    if not user_id:
        return {'success': False, 'message': f'Could not extract user_id from thread_id: {thread_id_str}'}

    try:
        from app.utils.rag_vectorstore import delete_by_thread
        delete_by_thread(thread_id_str, user_id)
        logger.info("Removed vectors from Milvus for thread %s", thread_id_str)

        # Optionally remove uploaded files
        try:
            for file_path in UPLOADED_FILES_DIR.glob(f"{thread_id_str}_*"):
                try:
                    file_path.unlink()
                    logger.info("Deleted uploaded file: %s", file_path)
                except Exception as e:
                    logger.warning("Failed to delete uploaded file %s: %s", file_path, e)
        except Exception as e:
            logger.warning("Error removing uploaded files: %s", e)

        return {'success': True, 'message': f'Thread {thread_id_str} deleted successfully'}
    except Exception as e:
        logger.error("Error deleting thread %s: %s", thread_id_str, e, exc_info=True)
        return {'success': False, 'message': f'Failed to delete thread: {str(e)}'}