# from typing import List, Dict
# import logging
# from app.utils.db import get_db

# logger = logging.getLogger(__name__)

# class Conversation:
#     """Model for handling conversation-related database operations"""
    
#     def __init__(self, user_id: int):
#         self.user_id = user_id
    
#     def create_conversation(self, title: str) -> int:
#         """Create a new conversation"""
#         try:
#             db = get_db()
#             cursor = db.execute(
#                 'INSERT INTO conversations (user_id, title) VALUES (?, ?)',
#                 (self.user_id, title)
#             )
#             db.commit()
#             return cursor.lastrowid
#         except Exception as e:
#             logger.error(f"Error creating conversation: {str(e)}")
#             raise
    
#     def get_conversations(self, limit: int = 4) -> List[Dict]:
#         """Get user's recent conversations"""
#         try:
#             db = get_db()
#             conversations = db.execute(
#                 '''SELECT id, title, created_at 
#                    FROM conversations 
#                    WHERE user_id = ? 
#                    ORDER BY created_at DESC 
#                    LIMIT ?''',
#                 (self.user_id, limit)
#             ).fetchall()
#             return [dict(conv) for conv in conversations]
#         except Exception as e:
#             logger.error(f"Error retrieving conversations: {str(e)}")
#             raise
    
#     def save_message(self, conversation_id: int, message: str, role: str) -> int:
#         """Save a message to a conversation"""
#         try:
#             db = get_db()
#             cursor = db.execute(
#                 '''INSERT INTO messages (conversation_id, content, role)
#                    VALUES (?, ?, ?)''',
#                 (conversation_id, message, role)
#             )
#             db.commit()
#             return cursor.lastrowid
#         except Exception as e:
#             logger.error(f"Error saving message: {str(e)}")
#             raise
    
#     def get_chat_history(self, conversation_id: int) -> List[Dict]:
#         """Get chat history for a conversation"""
#         try:
#             db = get_db()
#             messages = db.execute(
#                 '''SELECT id, content, role, created_at
#                    FROM messages
#                    WHERE conversation_id = ?
#                    ORDER BY created_at ASC''',
#                 (conversation_id,)
#             ).fetchall()
#             return [dict(msg) for msg in messages]
#         except Exception as e:
#             logger.error(f"Error retrieving chat history: {str(e)}")
#             raise 







from app.models.models import ConversationModel as Conversation