"""
Encryption utility for securely storing API keys
Uses Fernet symmetric encryption
"""
import os
import base64
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from app.config import Config

logger = logging.getLogger(__name__)

# Generate encryption key from SECRET_KEY
def _get_encryption_key():
    """Generate encryption key from SECRET_KEY"""
    secret_key = Config.SECRET_KEY.encode()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b'iqbal_ai_salt_2024',  # Fixed salt for consistency
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret_key))
    return key

_fernet = None

def _get_fernet():
    """Get or create Fernet instance"""
    global _fernet
    if _fernet is None:
        try:
            key = _get_encryption_key()
            _fernet = Fernet(key)
        except Exception as e:
            logger.error(f"Error initializing encryption: {str(e)}")
            raise
    return _fernet

def encrypt_api_key(api_key: str) -> str:
    """Encrypt an API key"""
    if not api_key:
        return ""
    try:
        fernet = _get_fernet()
        encrypted = fernet.encrypt(api_key.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Error encrypting API key: {str(e)}")
        raise

def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt an API key"""
    if not encrypted_key:
        return ""
    try:
        fernet = _get_fernet()
        decrypted = fernet.decrypt(encrypted_key.encode())
        return decrypted.decode()
    except Exception as e:
        logger.error(f"Error decrypting API key: {str(e)}")
        raise

def mask_api_key(api_key: str) -> str:
    """Mask an API key for display (shows only first 4 and last 4 characters)"""
    if not api_key or len(api_key) < 8:
        return "****"
    return f"{api_key[:4]}****{api_key[-4:]}"


