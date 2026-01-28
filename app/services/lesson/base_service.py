# """
# Base lesson service with common functionality
# """
# import logging
# from typing import Any, Dict, List, Optional
# # from langchain_groq import ChatGroq
# from langchain_ollama import ChatOllama

# from langchain_core.documents import Document
# from langchain_community.document_loaders import PyMuPDFLoader, UnstructuredWordDocumentLoader, TextLoader
# from langchain_community.embeddings import HuggingFaceEmbeddings
# from langchain_community.vectorstores import FAISS
# from langchain_text_splitters import RecursiveCharacterTextSplitter
# import tempfile
# import os

# logger = logging.getLogger(__name__)


# class BaseLessonService:
#     """
#     Base class for lesson services with common functionality
#     """
    
#     def __init__(self, groq_api_key: str):
#         """Initialize the base service with API key"""
#         self.api_key = groq_api_key
#         # self.llm = ChatGroq(
#         #     groq_api_key=groq_api_key,
#         #     model_name="llama-3.1-8b-instant",
#         #     temperature=0.1
#         # )
#         ollama_base_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
#         ollama_model = os.getenv('OLLAMA_MODEL', 'qwen2.5:3b')
#         self.llm = ChatOllama(
#             model="qwen2.5:1.5b",
#             base_url=ollama_base_url
#         )
#         logger.info(f"Base lesson service initialized with Ollama at {ollama_base_url} using model {ollama_model}")

#     def allowed_file(self, filename: str) -> bool:
#         """Check if file extension is supported"""
#         if not filename:
#             return False
        
#         allowed_extensions = {'.pdf', '.doc', '.docx', '.txt'}
#         return any(filename.lower().endswith(ext) for ext in allowed_extensions)

#     def _load_document(self, file_path: str, filename: str) -> List[Document]:
#         """Load document based on file type"""
#         try:
#             if filename.lower().endswith('.pdf'):
#                 loader = PyMuPDFLoader(file_path)
#             elif filename.lower().endswith(('.doc', '.docx')):
#                 loader = UnstructuredWordDocumentLoader(file_path)
#             elif filename.lower().endswith('.txt'):
#                 loader = TextLoader(file_path)
#             else:
#                 logger.error(f"Unsupported file type: {filename}")
#                 return []
            
#             documents = loader.load()
#             logger.info(f"Loaded {len(documents)} pages from {filename}")
#             return documents
            
#         except Exception as e:
#             logger.error(f"Error loading document {filename}: {str(e)}")
#             return []

#     def _sanitize_heading(self, heading: str) -> str:
#         """Sanitize heading text for DOCX generation"""
#         if not heading:
#             return "Untitled"
        
#         # Remove or replace problematic characters
#         heading = heading.replace('\n', ' ').replace('\r', ' ')
#         heading = heading.strip()
        
#         # Limit length
#         if len(heading) > 100:
#             heading = heading[:97] + "..."
        
#         return heading


"""
Base lesson service with common functionality
"""
import logging
from typing import Any, Dict, List, Optional
import os
import tempfile

from langchain_openai import ChatOpenAI
from langchain_core.documents import Document
from app.utils.llm_factory import create_llm
from langchain_community.document_loaders import PyMuPDFLoader, UnstructuredWordDocumentLoader, TextLoader
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter


logger = logging.getLogger(__name__)


class BaseLessonService:
    """
    Base class for lesson services with common functionality
    """
    
    def __init__(self, groq_api_key: str = None):
        """Initialize the base service with central LLM provider (same as chat/RAG).
        
        Args:
            groq_api_key: Optional API key. If None, uses central admin settings from SystemSettings.
                         This makes lesson features use the exact same LLM provider as chat/RAG.
        """
        self.api_key = groq_api_key
        
        # Use the same LLM factory as chat/RAG - gets provider and API key from admin settings
        # This ensures lesson features use the exact same central LLM configuration
        from app.utils.llm_factory import get_chat_model
        
        # Get user_id from session if available (for user-specific model selection)
        # Handle case where we're not in a request context (e.g., background tasks)
        user_id = None
        try:
            from flask import has_request_context, session
            if has_request_context():
                user_id = session.get('user_id')
        except Exception:
            # Not in a request context, use None
            pass
        
        # Use get_chat_model which handles:
        # - active_provider from SystemSettings
        # - API key from SystemSettings (encrypted) or env vars
        # - User-specific model selection if enabled
        # This is the SAME logic used by chat/RAG endpoints
        self.llm = get_chat_model(user_id=user_id)
        
        logger.info(f"Base lesson service initialized with central LLM provider (same as chat/RAG)")

    def allowed_file(self, filename: str) -> bool:
        """Check if file extension is supported"""
        if not filename:
            return False
        
        allowed_extensions = {'.pdf', '.doc', '.docx', '.txt'}
        return any(filename.lower().endswith(ext) for ext in allowed_extensions)

    def _load_document(self, file_path: str, filename: str) -> List[Document]:
        """Load document based on file type"""
        try:
            if filename.lower().endswith('.pdf'):
                loader = PyMuPDFLoader(file_path)
            elif filename.lower().endswith(('.doc', '.docx')):
                loader = UnstructuredWordDocumentLoader(file_path)
            elif filename.lower().endswith('.txt'):
                loader = TextLoader(file_path)
            else:
                logger.error(f"Unsupported file type: {filename}")
                return []
            
            documents = loader.load()
            logger.info(f"Loaded {len(documents)} pages from {filename}")
            return documents
            
        except Exception as e:
            logger.error(f"Error loading document {filename}: {str(e)}")
            return []

    def _sanitize_heading(self, heading: str) -> str:
        """Sanitize heading text for DOCX generation"""
        if not heading:
            return "Untitled"
        
        # Remove or replace problematic characters
        heading = heading.replace('\n', ' ').replace('\r', ' ')
        heading = heading.strip()
        
        # Limit length
        if len(heading) > 100:
            heading = heading[:97] + "..."
        
        return heading

    def invoke_llm(self, prompt: str) -> str:
        """
        Invoke vLLM LLM.
        
        Args:
            prompt: The prompt to send to the LLM
            
        Returns:
            The LLM response as a string
        """
        try:
            logger.info(f"[VLLM] Invoking LLM")
            response = self.llm.invoke(prompt)
            
            # Handle different response types
            if hasattr(response, 'content'):
                result = response.content
            elif isinstance(response, dict) and 'content' in response:
                result = response['content']
            else:
                result = str(response)
            
            logger.info(f"[VLLM] LLM invocation completed")
            return result
            
        except Exception as e:
            logger.error(f"Error invoking LLM: {str(e)}")
            raise

    def stream_llm(self, prompt: str):
        """
        Stream responses from vLLM LLM.
        
        Args:
            prompt: The prompt to send to the LLM
            
        Yields:
            Chunks of the LLM response
        """
        try:
            logger.info(f"[VLLM] Starting LLM stream")
            
            for chunk in self.llm.stream(prompt):
                if hasattr(chunk, 'content'):
                    yield chunk.content
                elif isinstance(chunk, dict) and 'content' in chunk:
                    yield chunk['content']
                else:
                    yield str(chunk)
            
            logger.info(f"[VLLM] LLM stream completed")
            
        except Exception as e:
            logger.error(f"Error streaming from LLM: {str(e)}")
            raise

    def create_prompt(self, template: str, **kwargs) -> str:
        """
        Create a prompt from a template with variable substitution.
        
        Args:
            template: The prompt template with {variables}
            **kwargs: Variables to substitute in the template
            
        Returns:
            The formatted prompt
        """
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.error(f"Missing template variable: {e}")
            raise ValueError(f"Template requires variable: {e}")
        except Exception as e:
            logger.error(f"Error creating prompt: {str(e)}")
            raise

    def extract_text_from_documents(self, documents: List[Document]) -> str:
        """
        Extract and combine text from multiple documents.
        
        Args:
            documents: List of Document objects
            
        Returns:
            Combined text from all documents
        """
        try:
            texts = []
            for doc in documents:
                if hasattr(doc, 'page_content'):
                    texts.append(doc.page_content)
                elif isinstance(doc, dict) and 'page_content' in doc:
                    texts.append(doc['page_content'])
            
            combined_text = "\n\n".join(texts)
            logger.info(f"Extracted {len(combined_text)} characters from {len(documents)} documents")
            return combined_text
            
        except Exception as e:
            logger.error(f"Error extracting text from documents: {str(e)}")
            return ""

    def chunk_text(self, text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[str]:
        """
        Split text into chunks for processing.
        
        Args:
            text: The text to chunk
            chunk_size: Maximum size of each chunk
            chunk_overlap: Overlap between chunks
            
        Returns:
            List of text chunks
        """
        try:
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                length_function=len,
            )
            chunks = text_splitter.split_text(text)
            logger.info(f"Split text into {len(chunks)} chunks")
            return chunks
            
        except Exception as e:
            logger.error(f"Error chunking text: {str(e)}")
            return [text]  # Return original text as single chunk on error

    def get_llm_status(self) -> Dict[str, Any]:
        """
        Get current LLM service status (supports both OpenAI and vLLM).
        
        Returns:
            Dictionary with status information
        """
        provider = os.getenv('LLM_PROVIDER', 'openai').lower()
        
        if provider == 'openai':
            return {
                'provider': 'openai',
                'model': os.getenv('OPENAI_MODEL', 'gpt-3.5-turbo'),
                'temperature': float(os.getenv('OPENAI_TEMPERATURE', '0.7')),
                'max_tokens': int(os.getenv('OPENAI_MAX_TOKENS', '1024')),
                'timeout': int(os.getenv('OPENAI_TIMEOUT', '60'))
            }
        else:
            return {
                'provider': 'vllm',
                'base_url': os.getenv('VLLM_API_BASE', 'http://69.28.92.113:8000/v1'),
                'model': os.getenv('VLLM_MODEL', 'Qwen/Qwen2.5-14B-Instruct'),
                'temperature': float(os.getenv('VLLM_TEMPERATURE', '0.7')),
                'max_tokens': int(os.getenv('VLLM_MAX_TOKENS', '1024')),
                'timeout': int(os.getenv('VLLM_TIMEOUT', '600'))
            }
    
    def get_vllm_status(self) -> Dict[str, Any]:
        """
        Legacy method name - redirects to get_llm_status for backward compatibility.
        
        Returns:
            Dictionary with status information
        """
        return self.get_llm_status()