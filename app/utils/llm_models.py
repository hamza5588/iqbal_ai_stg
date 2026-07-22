"""
LLM Models Configuration
Defines allowed models per provider for UI dropdowns and server-side validation
"""

from typing import Optional

# Groq Models
GROQ_MODELS = {
    "gpt_oss": [
        {"id": "openai/gpt-oss-120b", "name": "GPT-OSS 120B"},
    ],
    "llama": [
        {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B Versatile"},
        {"id": "llama-3.1-70b-versatile", "name": "Llama 3.1 70B Versatile"},
        {"id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B Instant"},
        {"id": "llama-3.1-70b-versatile", "name": "Llama 3.1 70B Versatile"},
        {"id": "llama-3-70b-8192", "name": "Llama 3 70B 8192"},
        {"id": "llama-3-8b-8192", "name": "Llama 3 8B 8192"},
    ],
    "qwen": [
        {"id": "qwen/qwen3.6-27b", "name": "Qwen3.6 27B"},
    ],
}

# OpenAI Models
OPENAI_MODELS = [
    {"id": "gpt-4o", "name": "GPT-4o"},
    {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
    {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
    {"id": "gpt-4", "name": "GPT-4"},
    {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo"},
]

# All Groq models (flattened)
ALL_GROQ_MODELS = [model for model_list in GROQ_MODELS.values() for model in model_list]

# Per-model max completion tokens (Groq docs). Requests above these return 400.
GROQ_MODEL_MAX_COMPLETION_TOKENS = {
    "qwen/qwen3.6-27b": 16384,
    "qwen/qwen3-32b": 16384,
    "openai/gpt-oss-120b": 65536,
    "openai/gpt-oss-20b": 65536,
    "llama-3.3-70b-versatile": 32768,
    "llama-3.1-8b-instant": 131072,
    "llama-3.1-70b-versatile": 32768,
    "llama-3-70b-8192": 8192,
    "llama-3-8b-8192": 8192,
}
GROQ_DEFAULT_MAX_COMPLETION_TOKENS = 8192

# OpenAI chat completion output caps (safe ceilings; API rejects higher values).
OPENAI_MODEL_MAX_COMPLETION_TOKENS = {
    "gpt-4o": 16384,
    "gpt-4o-mini": 16384,
    "gpt-4-turbo": 4096,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 4096,
}
OPENAI_DEFAULT_MAX_COMPLETION_TOKENS = 16384


def get_groq_max_completion_tokens(model_id: str) -> int:
    """Return Groq max_tokens ceiling for a model (completion limit, not context)."""
    if not model_id:
        return GROQ_DEFAULT_MAX_COMPLETION_TOKENS
    key = model_id.strip().lower()
    if key in GROQ_MODEL_MAX_COMPLETION_TOKENS:
        return GROQ_MODEL_MAX_COMPLETION_TOKENS[key]
    # Conservative default for unknown / preview models
    if "qwen" in key:
        return 16384
    if "gpt-oss" in key:
        return 65536
    return GROQ_DEFAULT_MAX_COMPLETION_TOKENS


def get_openai_max_completion_tokens(model_id: str) -> int:
    """Return OpenAI max_tokens ceiling for a model (completion limit, not context)."""
    if not model_id:
        return OPENAI_DEFAULT_MAX_COMPLETION_TOKENS
    key = model_id.strip().lower()
    if key in OPENAI_MODEL_MAX_COMPLETION_TOKENS:
        return OPENAI_MODEL_MAX_COMPLETION_TOKENS[key]
    if key.startswith("gpt-4o"):
        return 16384
    if key.startswith("gpt-4"):
        return 8192
    if key.startswith("gpt-3.5"):
        return 4096
    return OPENAI_DEFAULT_MAX_COMPLETION_TOKENS


def get_provider_max_completion_tokens(provider: str, model_id: str) -> int:
    """Unified completion-token ceiling for the active provider/model."""
    p = (provider or "").strip().lower()
    if p == "groq":
        return get_groq_max_completion_tokens(model_id)
    if p == "openai":
        return get_openai_max_completion_tokens(model_id)
    # vLLM / unknown: keep a generous but finite default
    return 16384


def clamp_max_tokens_for_model(
    provider: str,
    model_id: str,
    requested: Optional[int],
) -> Optional[int]:
    """Clamp requested max_tokens to the model’s supported completion limit."""
    if requested is None:
        return None
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return None
    if value < 1:
        return 1
    cap = get_provider_max_completion_tokens(provider, model_id)
    return min(value, cap)


def get_groq_available_models() -> dict:
    """Grouped Groq models for admin and user selection UIs."""
    return {key: GROQ_MODELS[key] for key in GROQ_MODELS}

# Get all model IDs for a provider
def get_model_ids_for_provider(provider: str) -> list:
    """Get list of model IDs for a provider"""
    if provider.upper() == "GROQ":
        return [model["id"] for model in ALL_GROQ_MODELS]
    elif provider.upper() == "OPENAI":
        return [model["id"] for model in OPENAI_MODELS]
    return []

# Validate model for provider
def is_valid_model_for_provider(model_id: str, provider: str) -> bool:
    """Check if a model ID is valid for the given provider"""
    return model_id in get_model_ids_for_provider(provider)

# Get default model for provider
def get_default_model_for_provider(provider: str) -> str:
    """Get default model ID for a provider"""
    if provider.upper() == "GROQ":
        return "openai/gpt-oss-120b"
    elif provider.upper() == "OPENAI":
        return "gpt-4o-mini"
    return "gpt-4o-mini"


