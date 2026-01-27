from typing import Dict, List
import logging
from app.utils.db import get_db
from app.models.database_models import ChatHistory as DBChatHistory

logger = logging.getLogger(__name__)


class Message:
    """Model for handling message-related database operations using ORM"""
    
    def __init__(self, conversation_id: int):
        self.conversation_id = conversation_id
    
    def save(self, content: str, role: str) -> int:
        """Save a message to the database"""
        try:
            db = get_db()
            chat_message = DBChatHistory(
                conversation_id=self.conversation_id,
                message=content,
                role=role
            )
            db.add(chat_message)
            db.commit()
            db.refresh(chat_message)
            return chat_message.id
        except Exception as e:
            logger.error(f"Error saving message: {str(e)}")
            db.rollback()
            raise
    
    @staticmethod
    def get_messages(conversation_id: int) -> List[Dict]:
        """Get all messages for a conversation"""
        try:
            db = get_db()
            messages = db.query(DBChatHistory).filter(
                DBChatHistory.conversation_id == conversation_id
            ).order_by(DBChatHistory.created_at.asc()).all()
            
            result = []
            for msg in messages:
                result.append({
                    'id': msg.id,
                    'content': msg.message,
                    'role': msg.role,
                    'created_at': msg.created_at.isoformat() if msg.created_at else None
                })
            return result
        except Exception as e:
            logger.error(f"Error retrieving messages: {str(e)}")
            raise