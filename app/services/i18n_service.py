"""
Internationalisation (i18n) service.

Uses JSON locale files (app/locales/{lang}.json) for platform UI strings.
No compile step required — files are loaded and cached at first access.

Supported languages: en (English), ur (Urdu), hi (Hindi).
AI responses are guided by injecting a language instruction into the system prompt.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Optional

from app.models.database_models import User
from app.services.school.errors import SchoolServiceError

SUPPORTED_LANGUAGES = {"en", "ur", "hi"}

_LOCALES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "locales")

_AI_LANGUAGE_INSTRUCTIONS = {
    "en": "",  # No extra instruction needed — English is default
    "ur": "Please respond in Urdu (اردو). Use clear, simple Urdu appropriate for students.",
    "hi": "Please respond in Hindi (हिंदी). Use clear, simple Hindi appropriate for students.",
}


@lru_cache(maxsize=10)
def _load_locale(lang: str) -> dict:
    """Load and cache a locale JSON file. Falls back to English on any error."""
    path = os.path.join(_LOCALES_DIR, f"{lang}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        if lang != "en":
            return _load_locale("en")
        return {}


def get_translation(key: str, lang: str = "en") -> str:
    """
    Return the translated string for a given key and language.
    Falls back to the English string if the key is missing in the target locale.
    Returns the key itself if not found anywhere.
    """
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en"
    locale = _load_locale(lang)
    if key in locale:
        return locale[key]
    # Fallback to English
    if lang != "en":
        en_locale = _load_locale("en")
        return en_locale.get(key, key)
    return key


def get_all_strings(lang: str = "en") -> dict:
    """Return all translation strings for a given language (for frontend use)."""
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en"
    base = dict(_load_locale("en"))
    if lang != "en":
        base.update(_load_locale(lang))
    return base


def set_user_language(db, *, user_id: int, lang_code: str) -> User:
    """Update a user's preferred language. Validates against SUPPORTED_LANGUAGES."""
    if lang_code not in SUPPORTED_LANGUAGES:
        raise SchoolServiceError(
            f"Unsupported language '{lang_code}'. Supported: {sorted(SUPPORTED_LANGUAGES)}",
            "invalid_language",
            400,
        )
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise SchoolServiceError("User not found", "not_found", 404)
    user.preferred_language = lang_code
    db.flush()
    return user


def get_ai_language_context(lang: str) -> str:
    """
    Return a system prompt snippet instructing the AI to respond in the user's language.
    Returns empty string for English (default behaviour).
    """
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en"
    return _AI_LANGUAGE_INSTRUCTIONS.get(lang, "")
