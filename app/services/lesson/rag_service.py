"""
RAG (Retrieval-Augmented Generation) service for lesson content
"""
import logging
import tempfile
import os
from typing import List, Dict, Any, Optional
from langchain_core.documents import Document
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Disable tqdm threading to prevent "cannot start new thread" errors
os.environ['TQDM_DISABLE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

logger = logging.getLogger(__name__)


class RAGService:
    """
    RAG service for semantic content retrieval and generation
    """
    
    def __init__(self):
        """Initialize RAG service with embeddings and text splitter"""
        # Lazy load embeddings to avoid blocking on initialization
        self._embeddings = None
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
            separators=["\n\n", "\n", " ", ""]
        )
        self.documents = []
        self.vector_store = None
        self.use_rag = False
        logger.info("RAG service initialized (embeddings will be loaded on first use)")
    
    @property
    def embeddings(self):
        """Lazy load embeddings only when needed"""
        if self._embeddings is None:
            try:
                logger.info("Loading embedding model: sentence-transformers/all-MiniLM-L6-v2")
                # Configure embeddings to disable threading and progress bars
                # Environment variables TQDM_DISABLE and TOKENIZERS_PARALLELISM are set at app startup
                self._embeddings = HuggingFaceEmbeddings(
                    model_name="sentence-transformers/all-MiniLM-L6-v2",
                    model_kwargs={
                        'device': 'cpu',
                        'trust_remote_code': False
                    },
                    encode_kwargs={
                        'normalize_embeddings': False
                    }
                )
                logger.info("Embedding model loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load embedding model: {str(e)}", exc_info=True)
                raise
        return self._embeddings

    def process_document(self, documents: List[Document], filename: str) -> Dict[str, Any]:
        """
        Process documents and create vector store
        
        Args:
            documents: List of documents to process
            filename: Name of the file being processed
            
        Returns:
            Dictionary with processing results
        """
        try:
            logger.info(f"Processing {len(documents)} documents for RAG")
            
            # Check if document is large enough for RAG
            total_text = "\n".join([doc.page_content for doc in documents])
            total_size_mb = len(total_text.encode('utf-8')) / (1024 * 1024)
            logger.info(f"Total document size: {total_size_mb:.2f}MB, {len(total_text)} characters")
            
            if len(total_text) < 400:  # Small documents don't need RAG
                logger.info("Document is small, RAG not needed")
                return {
                    'use_rag': False,
                    'message': 'Document is small, using direct processing'
                }
            
            # Load embeddings if not already loaded (lazy loading)
            logger.info("Ensuring embedding model is loaded...")
            _ = self.embeddings  # This will trigger lazy loading if needed
            
            # Split documents into chunks
            logger.info("Splitting documents into chunks...")
            all_chunks = self.text_splitter.split_documents(documents)
            logger.info(f"Created {len(all_chunks)} chunks from documents")
            
            # Create embeddings and vector store
            logger.info("Creating vector store with embeddings (this may take a while for large files)...")
            self.documents = all_chunks
            
            # For very large files, process in batches to avoid memory issues
            if len(all_chunks) > 100:
                logger.info(f"Large file detected ({len(all_chunks)} chunks), processing in batches...")
                # Process first batch
                batch_size = 50
                first_batch = all_chunks[:batch_size]
                self.vector_store = FAISS.from_documents(first_batch, self.embeddings)
                logger.info(f"Processed first batch of {len(first_batch)} chunks")
                
                # Process remaining chunks in batches
                for i in range(batch_size, len(all_chunks), batch_size):
                    batch = all_chunks[i:i+batch_size]
                    self.vector_store.add_documents(batch)
                    logger.info(f"Processed batch {i//batch_size + 1}: chunks {i} to {min(i+batch_size, len(all_chunks))}")
            else:
                # Small files - process all at once
                self.vector_store = FAISS.from_documents(all_chunks, self.embeddings)
            
            logger.info("Vector store created, saving to disk...")
            
            # Save the vector store
            self.vector_store.save_local("vector_store.faiss")
            logger.info("Vector store saved successfully")
            
            self.use_rag = True
            logger.info("Vector store created successfully")
            return {
                'use_rag': True,
                'chunks_count': len(all_chunks),
                'message': 'Vector store created successfully'
            }
            
        except Exception as e:
            logger.error(f"Error processing documents for RAG: {str(e)}", exc_info=True)
            return {
                'use_rag': False,
                'error': f'Failed to process documents: {str(e)}'
            }

    def retrieve_relevant_chunks(self, query: str, k: int = 5) -> List[Document]:
        """
        Retrieve relevant chunks based on query
        
        Args:
            query: Search query
            k: Number of chunks to retrieve
            
        Returns:
            List of relevant documents
        """
        try:
            if not self.vector_store:
                logger.warning("No vector store available")
                return []
            
            # Perform similarity search
            relevant_docs = self.vector_store.similarity_search(query, k=k)
            logger.info(f"Retrieved {len(relevant_docs)} relevant chunks for query")
            return relevant_docs
            
        except Exception as e:
            logger.error(f"Error retrieving relevant chunks: {str(e)}")
            return []

    def create_rag_prompt(self, user_prompt: str, relevant_chunks: List[Document], lesson_details: Optional[Dict[str, str]] = None) -> str:
        """
        Create RAG prompt with retrieved context
        
        Args:
            user_prompt: User's prompt
            relevant_chunks: Retrieved relevant chunks
            lesson_details: Additional lesson details
            
        Returns:
            Formatted RAG prompt
        """
        try:
            # Combine relevant chunks
            context = "\n\n".join([chunk.page_content for chunk in relevant_chunks])
            
            # Create base prompt
            # prompt = f"""
            # You are an intelligent and articulate assistant that answers questions using a provided document.
            # If the document does not contain the information, you may use your general knowledge.

            # INSTRUCTIONS:
            # 1. Read the user's question carefully and focus only on what they are asking.
            # 2. Understand the user’s intent and adjust the level of detail accordingly:
            # - If the user asks for a **concise**, **short**, or **1-line** answer → respond briefly and directly in one line.
            # - Otherwise (by default) → provide a **comprehensive, detailed, and well-structured** explanation with clear sections and examples when useful.
            # 3. Use the document as the primary information source. If the answer is not found in the document, use accurate general knowledge.
            # 4. If the user asks for a **lesson**, generate it in a detailed and educational format with headings and subheadings.
            # 5. Do **not** mention phrases like "based on the provided document" or "from the context" in your answer.
            # 6. Always respond clearly, professionally, and in an engaging tone.
            prompt = f"""
            Prof Potter's Role: Prof. Potter assists the Faculty in generating structured, logical, and engaging class-lessons for students.

            Identify the Subject and Topic.

            Determine Prerequisites:

            Identify background material or prior lessons students must understand.

            Offer to review and revise prerequisite lessons.

            Develop the Class Lesson:

            Build lessons as a series of short, simple lectures.

            Ensure each lecture is self-explanatory.

            Combine all lectures (plus prerequisites) into the full class-lesson.

            Logical Flow:

            Begin from the simplest concepts and build complexity step by step.

            Maintain logical continuity—no disjointed statements.

            Faculty Interaction:

            Ask questions for clarity.

            Occasionally explain what part of the lesson is being developed next.

            Incorporate Faculty suggestions when valid.

            Creativity & Feedback:

            Praise Faculty for creative teaching methods or unique perspectives.

            Document Context:
            {context}

            User Request:
            {user_prompt}
            """

            logger.info(f"Created RAG prompt with {len(context)} characters of context")
            return prompt
        except Exception as e:
            logger.error(f"Error creating RAG prompt: {str(e)}")
            return f"Please answer the following question: {user_prompt}"
