try:
    from .models import UserModel, ChatModel, VectorStoreModel, ConversationModel
    __all__ = ['UserModel', 'ChatModel', 'VectorStoreModel', 'ConversationModel']
except ImportError:
    # Lightweight import path used in tests / CI where ML packages are not installed.
    __all__ = []