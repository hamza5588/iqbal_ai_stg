# app/services/__init__.py
try:
    from .chat_service import ChatService
    from .prompt_service import PromptService
    from .lesson_service import LessonService
    from .chatbot_service import DocumentChatBot as ChatbotService
    __all__ = ['ChatService', 'PromptService', 'LessonService', 'ChatbotService']
except ImportError:
    # Lightweight import path used in tests / CI where ML packages are not installed.
    __all__ = []