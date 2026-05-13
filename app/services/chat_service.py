# # app/services/chat_service.py
# from typing import List, Dict, Optional, Any
# from app.models import ChatModel, ConversationModel, VectorStoreModel
# import logging
# from datetime import datetime

# logger = logging.getLogger(__name__)

# class ChatService:
#     """Service class for handling chat-related business logic"""
    
#     def __init__(self, user_id: int, api_key: str):
#         self.user_id = user_id
#         self.chat_model = ChatModel(api_key)
#         self.conversation_model = ConversationModel(user_id)
#         self.vector_store = VectorStoreModel()
        
#     def format_chat_history(self, history: List[Dict]) -> List[Dict]:
#         """Format chat history for the LLM"""
#         formatted_history = []
#         for msg in history:
#             # Map 'bot' role to 'assistant' and ensure we access the correct message field
#             role = 'assistant' if msg.get('role') == 'bot' else msg.get('role', 'user')
#             content = msg.get('message', '')  # Get message content with default empty string
            
#             if content and role:  # Only add if we have both content and role
#                 formatted_history.append({
#                     'role': role,
#                     'content': content
#                 })
#         return formatted_history
        
#     def process_message(self, message: str, 
#                        conversation_id: Optional[int] = None,
#                        system_prompt: Optional[str] = None) -> Dict[str, Any]:
#         """Process a user message and generate a response"""
#         try:
#             # Create new conversation if needed
#             if not conversation_id:
#                 conversation_id = self.conversation_model.create_conversation(
#                     title=message[:50]
#                 )
            
#             # Save user message
#             self.conversation_model.save_message(
#                 conversation_id=conversation_id,
#                 message=message,
#                 role='user'
#             )
            
#             # Get relevant context from vector store
#             context = ""
#             try:
#                 if self.vector_store._vectorstore:
#                     relevant_docs = self.vector_store.search_similar(message, k=3)
#                     if relevant_docs:
#                         context = "Using the provided documents, I found this relevant information:\n\n" + "\n".join(
#                             [f"• {doc.page_content.strip()}" for doc in relevant_docs]
#                         )
#                         logger.info("Found relevant context from documents")
#             except Exception as e:
#                 logger.warning(f"Error retrieving context: {str(e)}")
            
#             # Get and format chat history
#             raw_history = self.conversation_model.get_chat_history(conversation_id)
#             formatted_history = self.format_chat_history(raw_history)
            
#             # For debugging
#             logger.debug(f"Formatted history: {formatted_history}")
            
#             # Use the provided system prompt or default to teaching framework
#             if not system_prompt:
#                 system_prompt = self.DEFAULT_PROMPT
            
#             # Generate response
#             response = self.chat_model.generate_response(
#                 input_text=message,
#                 system_prompt=system_prompt,
#                 chat_history=formatted_history
#             )
            
#             # Save bot response
#             self.conversation_model.save_message(
#                 conversation_id=conversation_id,
#                 message=response,
#                 role='bot'
#             )
            
#             return {
#                 'response': response,
#                 'conversation_id': conversation_id
#             }
            
#         except Exception as e:
#             logger.error(f"Error processing message: {str(e)}")
#             raise

#     def get_recent_conversations(self, limit: int = 4) -> List[Dict[str, Any]]:
#         """Get user's recent conversations"""
#         try:
#             return self.conversation_model.get_conversations(limit=limit)
#         except Exception as e:
#             logger.error(f"Error retrieving conversations: {str(e)}")
#             raise

#     def get_conversation_messages(self, conversation_id: int) -> List[Dict[str, Any]]:
#         """Get messages for a specific conversation"""
#         try:
#             raw_messages = self.conversation_model.get_chat_history(conversation_id)
#             return [
#                 {
#                     'role': msg.get('role', 'user'),
#                     'message': msg.get('message', ''),
#                     'created_at': msg.get('created_at', datetime.now().isoformat())
#                 }
#                 for msg in raw_messages
#             ]
#         except Exception as e:
#             logger.error(f"Error retrieving messages: {str(e)}")
#             raise

#     def create_conversation(self, title: str) -> int:
#         """Create a new conversation"""
#         try:
#             return self.conversation_model.create_conversation(title)
#         except Exception as e:
#             logger.error(f"Error creating conversation: {str(e)}")
#             raise

#     def delete_conversation(self, conversation_id: int) -> None:
#         """Delete a conversation"""
#         try:
#             self.conversation_model.delete_conversation(conversation_id)
#         except Exception as e:
#             logger.error(f"Error deleting conversation: {str(e)}")
#             raise

#     def clean_old_conversations(self, max_conversations: int = 50) -> None:
#         """Clean up old conversations beyond the maximum limit"""
#         try:
#             conversations = self.conversation_model.get_conversations()
#             if len(conversations) > max_conversations:
#                 for conv in conversations[max_conversations:]:
#                     self.delete_conversation(conv['id'])
#         except Exception as e:
#             logger.error(f"Error cleaning old conversations: {str(e)}")
#             raise



# app/services/chat_service.py
# app/services/chat_service.py
# app/services/chat_service.py
# app/services/chat_service.py
from typing import List, Dict, Optional, Any, Tuple
from app.models import ChatModel, ConversationModel, VectorStoreModel
import logging
from datetime import datetime
import re
import httpx
import time
from app.utils.constants import MAX_MESSAGE_WINDOW, MAX_CONTEXT_TOKENS, SUMMARY_THRESHOLD, DEFAULT_PROMPT
import tiktoken

logger = logging.getLogger(__name__)

class ChatService:
    """Service class for handling chat-related business logic"""
    
    def __init__(self, user_id: int, api_key: str):
        self.user_id = user_id
        self.api_key = api_key
        self._chat_model = None  # Will be lazily initialized
        self.conversation_model = ConversationModel(user_id)
        self.vector_store = VectorStoreModel(user_id=user_id)
        self._document_context_cache = {}  # Cache for document contexts
        self._cache_ttl = 180  # Reduced from 300 (3 minutes instead of 5)
        self._cache_cleanup_interval = 60  # Clean up cache every minute
        self._last_cache_cleanup = time.time()
        self._tokenizer = tiktoken.get_encoding("cl100k_base")  # For token counting
        self._system_prompt = DEFAULT_PROMPT  # Use the default prompt from constants
    
    @property
    def chat_model(self):
        """Lazy initialization of chat model with caching"""
        if not self._chat_model:
            self._chat_model = ChatModel(api_key=self.api_key, user_id=self.user_id)
        return self._chat_model

    def _create_new_chat_model(self):
        """Create a fresh chat model instance for each conversation"""
        return ChatModel(api_key=self.api_key, user_id=self.user_id)
        
    def _count_tokens(self, text: str) -> int:
        """Count the number of tokens in a text string."""
        return len(self._tokenizer.encode(text))

    def _summarize_history(self, history: List[Dict]) -> str:
        """Summarize old messages in the conversation history."""
        try:
            # Get the most recent messages
            recent_messages = history[-MAX_MESSAGE_WINDOW:]
            
            # Create a summary of older messages
            older_messages = history[:-MAX_MESSAGE_WINDOW]
            if not older_messages:
                return ""
                
            summary_prompt = "Summarize the following conversation in a concise way, maintaining the context and key points:\n\n"
            for msg in older_messages:
                role = msg.get('role', 'user')
                content = msg.get('message', '')
                summary_prompt += f"{role}: {content}\n"
            
            # Use the chat model to generate a summary
            summary = self.chat_model.generate_response(
                input_text=summary_prompt,
                system_prompt="You are a helpful assistant that summarizes conversations concisely while maintaining context and key points.",
                chat_history=[]
            )
            
            return f"[Previous conversation summary: {summary}]\n\n"
        except Exception as e:
            logger.error(f"Error summarizing history: {str(e)}")
            return ""

    def _manage_context_window(self, history: List[Dict]) -> Tuple[List[Dict], str]:
        """Manage the context window by implementing a sliding window and summarization."""
        if len(history) <= MAX_MESSAGE_WINDOW:
            return history, ""
            
        # If we have more messages than the window size, summarize older ones
        if len(history) > SUMMARY_THRESHOLD:
            summary = self._summarize_history(history)
            recent_messages = history[-MAX_MESSAGE_WINDOW:]
            return recent_messages, summary
            
        # Otherwise, just keep the most recent messages
        return history[-MAX_MESSAGE_WINDOW:], ""

    def format_chat_history(self, history: List[Dict]) -> List[Dict]:
        """Format chat history for the LLM with memory management."""
        # Apply memory management
        managed_history, summary = self._manage_context_window(history)
        
        formatted_history = []
        if summary:
            formatted_history.append({
                'role': 'system',
                'content': summary
            })
            
        for msg in managed_history:
            role = 'assistant' if msg.get('role') == 'bot' else msg.get('role', 'user')
            content = msg.get('message', '')
            
            if content and role:
                formatted_history.append({
                    'role': role,
                    'content': content
                })
                
        return formatted_history
        
    def _cleanup_cache(self):
        """Clean up expired cache entries"""
        current_time = time.time()
        if current_time - self._last_cache_cleanup >= self._cache_cleanup_interval:
            expired_keys = [
                key for key, value in self._document_context_cache.items()
                if current_time - value['timestamp'] >= self._cache_ttl
            ]
            for key in expired_keys:
                del self._document_context_cache[key]
            self._last_cache_cleanup = current_time

    def get_document_context(self, message: str) -> str:
        """Get relevant context from uploaded documents with optimized caching"""
        try:
            # Clean up cache periodically
            self._cleanup_cache()
            
            # Check cache first
            cache_key = f"{self.user_id}:{message}"
            cached_result = self._document_context_cache.get(cache_key)
            if cached_result and time.time() - cached_result['timestamp'] < self._cache_ttl:
                return cached_result['context']

            if self.vector_store._vectorstore:
                # Check if the query is asking about a specific page
                page_match = re.search(r'page\s*(\d+)', message.lower())
                page_number = int(page_match.group(1)) if page_match else None
                
                # Reduce k from 3 to 2 for faster search
                relevant_docs = self.vector_store.search_similar(message, k=2)
                if relevant_docs:
                    context_parts = []
                    for doc in relevant_docs:
                        page = doc.metadata.get('page', 1)
                        if page_number is not None and page != page_number:
                            continue
                        context_parts.append(
                            f"Page {page}:\n{doc.page_content.strip()}"
                        )
                    
                    if context_parts:
                        context = "Based on the uploaded documents:\n\n" + "\n\n".join(context_parts)
                        # Cache the result
                        self._document_context_cache[cache_key] = {
                            'context': context,
                            'timestamp': time.time()
                        }
                        return context
                    elif page_number is not None:
                        return f"I couldn't find any relevant information on page {page_number}."
            
            return ""
            
        except Exception as e:
            logger.warning(f"Error retrieving document context: {str(e)}")
            return ""
            
    def process_message(self, message: str, 
                       conversation_id: Optional[int] = None) -> Dict[str, Any]:
        """Process a user message and generate a response"""
        try:
            # Create new conversation if needed
            if not conversation_id:
                conversation_id = self.conversation_model.create_conversation(
                    title=message[:50]
                )
                # For new conversations, don't include any chat history
                formatted_history = []
            else:
                # Verify conversation ownership before accessing
                conversation = self.conversation_model.get_conversation_by_id(conversation_id)
                if not conversation:
                    logger.warning(f"User {self.user_id} attempted to use conversation {conversation_id} without ownership")
                    # Create a new conversation if the provided one doesn't belong to this user
                    conversation_id = self.conversation_model.create_conversation(
                        title=message[:50]
                    )
                    formatted_history = []
                else:
                    # Get and format chat history only for existing conversations
                    raw_history = self.conversation_model.get_chat_history(conversation_id)
                    formatted_history = self.format_chat_history(raw_history)
            
            # Save user message
            self.conversation_model.save_message(
                conversation_id=conversation_id,
                message=message,
                role='user'
            )
            
            # Get document context
            document_context = self.get_document_context(message)
            
            # For debugging
            logger.debug(f"Formatted history: {formatted_history}")
            
            # Enhance system prompt with document context
            enhanced_prompt = self._system_prompt

            # Phase 1: prepend AI personality modifier and language instruction
            try:
                from app.utils.db import get_db as _get_db
                from app.models.database_models import User as _DBUser
                import app.services.personality_service as _ps
                import app.services.i18n_service as _i18n
                _db = _get_db()
                _user = _db.query(_DBUser).filter_by(id=self.user_id).first()
                if _user:
                    _personality_mod = _ps.get_system_prompt_modifier(_db, user=_user)
                    _lang_mod = _i18n.get_ai_language_context(_user.preferred_language)
                    _prefix = "\n".join(filter(None, [_personality_mod, _lang_mod]))
                    if _prefix:
                        enhanced_prompt = _prefix + "\n\n" + enhanced_prompt
            except Exception:
                pass  # Personality/language injection is non-blocking

            if document_context:
                enhanced_prompt = f"{enhanced_prompt}\n\nDocument Context:\n{document_context}\n\nWhen answering questions, prioritize information from the provided documents. If the answer is not in the documents, clearly state that and provide a general explanation based on your knowledge."
            
            # Generate response
            response = self.chat_model.generate_response(
                input_text=message,
                system_prompt=enhanced_prompt,
                chat_history=formatted_history
            )
            
            # Save bot response
            self.conversation_model.save_message(
                conversation_id=conversation_id,
                message=response,
                role='bot'
            )
            
            return {
                'response': response,
                'conversation_id': conversation_id
            }
            
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            raise

    def get_recent_conversations(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Get user's recent conversations.

        Default is intentionally high so the sidebar can show full history
        (ChatGPT-style scrolling) instead of truncating to only a few items.
        """
        try:
            return self.conversation_model.get_conversations(limit=limit)
        except Exception as e:
            logger.error(f"Error retrieving conversations: {str(e)}")
            raise

    def get_conversation_messages(self, conversation_id: int) -> List[Dict[str, Any]]:
        """Get messages for a specific conversation - verifies user ownership"""
        try:
            # Verify conversation ownership before retrieving messages
            conversation = self.conversation_model.get_conversation_by_id(conversation_id)
            if not conversation:
                logger.warning(f"User {self.user_id} attempted to access conversation {conversation_id} without ownership")
                return []
            
            raw_messages = self.conversation_model.get_chat_history(conversation_id)
            return [
                {
                    'role': msg.get('role', 'user'),
                    'message': msg.get('message', ''),
                    'created_at': msg.get('created_at', datetime.now().isoformat())
                }
                for msg in raw_messages
            ]
        except Exception as e:
            logger.error(f"Error retrieving messages: {str(e)}")
            raise

    def create_conversation(self, title: str) -> int:
        """Create a new conversation and clear user vector store context"""
        try:
            # Clear user vector store documents for a truly fresh chat
            self.vector_store.delete_user_documents()
            return self.conversation_model.create_conversation(title)
        except Exception as e:
            logger.error(f"Error creating conversation: {str(e)}")
            raise

    def delete_conversation(self, conversation_id: int) -> None:
        """Delete a conversation"""
        try:
            self.conversation_model.delete_conversation(conversation_id)
        except Exception as e:
            logger.error(f"Error deleting conversation: {str(e)}")
            raise
    
    def update_conversation_title(self, conversation_id: int, new_title: str) -> bool:
        """Update the title of a conversation"""
        try:
            return self.conversation_model.update_conversation_title(conversation_id, new_title)
        except Exception as e:
            logger.error(f"Error updating conversation title: {str(e)}")
            raise
    
    def get_conversation_details(self, conversation_id: int) -> Dict[str, Any]:
        """Get conversation details including title"""
        try:
            return self.conversation_model.get_conversation_by_id(conversation_id)
        except Exception as e:
            logger.error(f"Error retrieving conversation details: {str(e)}")
            raise

    def duplicate_conversation(self, conversation_id: int) -> int:
        """
        Duplicate a conversation (including all of its messages) for the current user.

        Returns the new conversation ID.
        """
        try:
            new_id = self.conversation_model.duplicate_conversation(conversation_id)
            if new_id is None:
                raise ValueError(
                    f"Conversation {conversation_id} not found or access denied for user {self.user_id}"
                )
            return new_id
        except Exception as e:
            logger.error(f"Error duplicating conversation: {str(e)}")
            raise

    def duplicate_conversation(self, conversation_id: int) -> int:
        """
        Duplicate a conversation (including all of its messages) for the current user.

        Returns the new conversation ID.
        """
        try:
            new_id = self.conversation_model.duplicate_conversation(conversation_id)
            if new_id is None:
                raise ValueError(
                    f"Conversation {conversation_id} not found or access denied for user {self.user_id}"
                )
            return new_id
        except Exception as e:
            logger.error(f"Error duplicating conversation: {str(e)}")
            raise

    def clean_old_conversations(self, max_conversations: int = 50) -> None:
        """Clean up old conversations beyond the maximum limit"""
        try:
            conversations = self.conversation_model.get_conversations()
            if len(conversations) > max_conversations:
                for conv in conversations[max_conversations:]:
                    self.delete_conversation(conv['id'])
        except Exception as e:
            logger.error(f"Error cleaning old conversations: {str(e)}")
            raise

    def reset_all_conversations(self) -> None:
        """Delete all conversations for the current user"""
        try:
            conversations = self.conversation_model.get_conversations()
            for conv in conversations:
                self.delete_conversation(conv['id'])
        except Exception as e:
            logger.error(f"Error resetting conversations: {str(e)}")
            raise

    def get_token_usage(self) -> Dict[str, Any]:
        """Get current token usage information"""
        try:
            return self.chat_model.get_token_usage()
        except Exception as e:
            logger.error(f"Error getting token usage: {str(e)}")
            return {
                'daily_limit': '100,000',
                'used_tokens': '0',
                'requested_tokens': '0',
                'wait_time': None
            }
