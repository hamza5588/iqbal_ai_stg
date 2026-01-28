"""
LLM Models Configuration
Defines allowed models per provider for UI dropdowns and server-side validation
"""

# Groq Models
GROQ_MODELS = {
    "qwen": [
        {"id": "qwen2.5-72b-instruct", "name": "Qwen 2.5 72B Instruct"},
        {"id": "qwen2.5-32b-instruct", "name": "Qwen 2.5 32B Instruct"},
        {"id": "qwen2.5-14b-instruct", "name": "Qwen 2.5 14B Instruct"},
        {"id": "qwen2.5-7b-instruct", "name": "Qwen 2.5 7B Instruct"},
    ],
    "llama": [
        {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B Versatile"},
        {"id": "llama-3.1-70b-versatile", "name": "Llama 3.1 70B Versatile"},
        {"id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B Instant"},
        {"id": "llama-3.1-70b-versatile", "name": "Llama 3.1 70B Versatile"},
        {"id": "llama-3-70b-8192", "name": "Llama 3 70B 8192"},
        {"id": "llama-3-8b-8192", "name": "Llama 3 8B 8192"},
    ]
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
        return "llama-3.3-70b-versatile"
    elif provider.upper() == "OPENAI":
        return "gpt-4o-mini"
    return "gpt-4o-mini"


