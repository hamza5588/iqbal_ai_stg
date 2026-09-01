"""
LLM Factory for dynamic LLM initialization
Supports OpenAI, Groq, and vLLM providers based on admin/user settings
"""
import os
import logging
from typing import Optional
from langchain_openai import ChatOpenAI
from app.config import Config
from app.utils.llm_models import (
    get_default_model_for_provider,
    is_valid_model_for_provider,
    get_model_ids_for_provider,
    get_groq_max_completion_tokens,
    get_openai_max_completion_tokens,
    clamp_max_tokens_for_model,
)
from app.utils.encryption import decrypt_api_key
from app.utils.llm_gateway import wrap_llm_with_telemetry

logger = logging.getLogger(__name__)

# Try to import ChatGroq, but don't fail if it's not installed
try:
    from langchain_groq import ChatGroq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("langchain-groq not available. Groq provider will not work.")


def _resolve_stored_api_key(provider: str) -> Optional[str]:
    """Read and decrypt a provider API key from SystemSettings when env is unset."""
    try:
        from app.utils.db import get_db
        from app.models.database_models import SystemSettings

        db = get_db()
        setting = db.query(SystemSettings).filter(
            SystemSettings.key == f"{provider.lower()}_api_key"
        ).first()
        if setting and setting.value:
            return decrypt_api_key(setting.value)
    except Exception as exc:
        logger.debug("Could not read stored API key for %s: %s", provider, exc)
    return None


def get_chat_model(user_id: Optional[int] = None, **kwargs):
    """
    Get chat model based on admin settings and user preferences.
    This is the main entry point for creating LLM instances.
    
    Args:
        user_id: User ID for user-specific model selection (optional)
        **kwargs: Additional parameters passed to create_llm
    
    Returns:
        LLM instance (ChatOpenAI or ChatGroq)
    """
    try:
        from app.utils.db import get_db
        from app.models.database_models import SystemSettings, UserSettings
        
        db = get_db()
        
        # Get active provider from admin settings
        provider_setting = db.query(SystemSettings).filter(
            SystemSettings.key == 'active_provider'
        ).first()
        
        active_provider = (provider_setting.value.upper() if provider_setting 
                          else kwargs.get('provider', Config.LLM_PROVIDER).upper())
        
        # Get provider-specific settings
        provider_key = active_provider.lower()
        
        # Get API key from admin settings (encrypted)
        api_key_setting = db.query(SystemSettings).filter(
            SystemSettings.key == f'{provider_key}_api_key'
        ).first()
        
        api_key = None
        if api_key_setting and api_key_setting.value:
            try:
                api_key = decrypt_api_key(api_key_setting.value)
                logger.debug(f"Successfully decrypted {active_provider} API key from database")
            except Exception as e:
                logger.warning(f"Error decrypting API key: {str(e)}")
                # Fallback to environment variable
                if active_provider == 'OPENAI':
                    api_key = os.getenv('OPENAI_API_KEY')
                elif active_provider == 'GROQ':
                    api_key = os.getenv('GROQ_API_KEY')
                logger.debug(f"Using environment variable for {active_provider} API key")
        else:
            # No API key in settings, use environment
            logger.debug(f"No {active_provider} API key in database settings, checking environment")
            if active_provider == 'OPENAI':
                api_key = os.getenv('OPENAI_API_KEY')
            elif active_provider == 'GROQ':
                api_key = os.getenv('GROQ_API_KEY')
        
        # Validate API key exists
        if not api_key:
            raise ValueError(
                f"{active_provider} API key is not configured. "
                f"Please configure it in the Admin Panel settings."
            )
        
        logger.info(f"Retrieved {active_provider} API key (length: {len(api_key) if api_key else 0})")
        
        # Determine model to use
        model_to_use = None
        
        # Check if user model selection is enabled for this provider
        allow_user_selection_setting = db.query(SystemSettings).filter(
            SystemSettings.key == f'{provider_key}_allow_user_model_selection'
        ).first()
        
        allow_user_selection = (
            allow_user_selection_setting and 
            allow_user_selection_setting.value.lower() == 'true'
        )
        
        # If user selection is allowed and user_id is provided, check user preference
        if allow_user_selection and user_id:
            user_settings = db.query(UserSettings).filter(
                UserSettings.user_id == user_id
            ).first()
            
            if user_settings and user_settings.selected_model:
                # Validate user's selected model
                if is_valid_model_for_provider(user_settings.selected_model, active_provider):
                    model_to_use = user_settings.selected_model
                    logger.info(f"Using user-selected model: {model_to_use} for user {user_id}")
                else:
                    logger.warning(
                        f"User {user_id} selected invalid model {user_settings.selected_model} "
                        f"for provider {active_provider}, falling back to default"
                    )
        
        # If no user model selected, use admin default
        if not model_to_use:
            default_model_setting = db.query(SystemSettings).filter(
                SystemSettings.key == f'{provider_key}_default_model'
            ).first()
            
            if default_model_setting and default_model_setting.value:
                model_to_use = default_model_setting.value
            else:
                # Fallback to hardcoded default
                model_to_use = get_default_model_for_provider(active_provider)

        # Groq retired qwen/qwen3-32b (Jul 17 2026); map to current Qwen replacement.
        if (model_to_use or "").strip().lower() == "qwen/qwen3-32b":
            logger.warning(
                "Model qwen/qwen3-32b is retired on Groq; using qwen/qwen3.6-27b instead"
            )
            model_to_use = "qwen/qwen3.6-27b"
        
        # Override with explicit model_name if provided
        if kwargs.get('model_name'):
            model_to_use = kwargs['model_name']
            if (model_to_use or "").strip().lower() == "qwen/qwen3-32b":
                model_to_use = "qwen/qwen3.6-27b"

        # Clamp completion tokens to the *resolved* model limit (Qwen=16384, etc.).
        # Prevents 400 errors when callers pass RAG_RESPONSE_MAX_TOKENS=25000.
        if kwargs.get("max_tokens") is not None:
            clamped = clamp_max_tokens_for_model(
                active_provider.lower(),
                model_to_use,
                kwargs.get("max_tokens"),
            )
            if clamped != kwargs.get("max_tokens"):
                logger.warning(
                    "Clamping max_tokens from %s to %s for %s/%s",
                    kwargs.get("max_tokens"),
                    clamped,
                    active_provider.lower(),
                    model_to_use,
                )
            kwargs["max_tokens"] = clamped
        
        # Create LLM instance
        logger.info(f"Creating LLM with provider={active_provider.lower()}, model={model_to_use}, has_api_key={bool(api_key)}")
        return create_llm(
            provider=active_provider.lower(),
            api_key=api_key,
            model_name=model_to_use,
            **{k: v for k, v in kwargs.items() if k != 'provider' and k != 'model_name' and k != 'api_key'}
        )
        
    except Exception as e:
        logger.error(f"Error in get_chat_model: {str(e)}", exc_info=True)
        # Try to preserve provider from kwargs or get from settings
        fallback_provider = kwargs.get('provider')
        if not fallback_provider:
            try:
                from app.utils.db import get_db
                from app.models.database_models import SystemSettings
                db = get_db()
                setting = db.query(SystemSettings).filter(SystemSettings.key == 'active_provider').first()
                if setting:
                    fallback_provider = setting.value.lower()
                else:
                    setting = db.query(SystemSettings).filter(SystemSettings.key == 'llm_provider').first()
                    fallback_provider = setting.value.lower() if setting else Config.LLM_PROVIDER.lower()
            except:
                fallback_provider = Config.LLM_PROVIDER.lower()
        
        # Fallback to original create_llm behavior but preserve provider
        logger.warning(f"Falling back to create_llm with provider={fallback_provider}")
        return create_llm(provider=fallback_provider, **kwargs)


def create_llm(
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
    provider: Optional[str] = None
) -> ChatOpenAI:
    """
    Create an LLM instance based on provider selection.
    
    Args:
        temperature: Override default temperature (optional)
        max_tokens: Override default max_tokens (optional)
        timeout: Override default timeout (optional)
        model_name: Override default model name (optional)
        api_key: Override default API key (optional, used for OpenAI and Groq)
        provider: Override LLM_PROVIDER setting (optional: 'openai', 'groq', 'vllm')
    
    Returns:
        LLM instance configured for the selected provider (ChatOpenAI or ChatGroq)
        
    Environment Variables:
        LLM_PROVIDER: 'openai', 'groq', or 'vllm' (default: 'openai')
        
        For OpenAI:
            OPENAI_API_KEY: Your OpenAI API key (required if LLM_PROVIDER='openai')
            OPENAI_MODEL: Model name (default: 'gpt-3.5-turbo')
            OPENAI_TEMPERATURE: Temperature (default: 0.7)
            OPENAI_MAX_TOKENS: Max tokens (default: 1024)
            OPENAI_TIMEOUT: Timeout in seconds (default: 60)
            
        For Groq:
            GROQ_API_KEY: Your Groq API key (required if LLM_PROVIDER='groq')
            GROQ_MODEL: Model name (default: 'openai/gpt-oss-120b')
            GROQ_TEMPERATURE: Temperature (default: 0.7)
            GROQ_MAX_TOKENS: Max tokens (default: 1024)
            GROQ_TIMEOUT: Timeout in seconds (default: 60)
            For Qwen models (qwen/...): reasoning is off by default (reasoning_effort=none,
            reasoning_format=hidden). Override with GROQ_REASONING_EFFORT and GROQ_REASONING_FORMAT.
            
        For vLLM:
            VLLM_API_BASE: vLLM API base URL (default: 'http://69.28.92.113:8000/v1')
            VLLM_MODEL: Model name (default: 'Qwen/Qwen2.5-14B-Instruct')
            VLLM_TEMPERATURE: Temperature (default: 0.7)
            VLLM_MAX_TOKENS: Max tokens (default: 1024)
            VLLM_TIMEOUT: Timeout in seconds (default: 600)
    """
    # Resolve provider in a way that respects Admin settings (same as RAG/global LLM)
    # Priority:
    # 1. Explicit `provider` argument
    # 2. SystemSettings.active_provider (Admin panel)
    # 3. Legacy SystemSettings.llm_provider
    # 4. Config.LLM_PROVIDER (default)
    resolved_provider = provider

    if resolved_provider is None:
        try:
            from app.utils.db import get_db
            from app.models.database_models import SystemSettings

            db = get_db()

            # Check new active_provider setting first
            setting = db.query(SystemSettings).filter(
                SystemSettings.key == "active_provider"
            ).first()

            if setting and setting.value:
                resolved_provider = setting.value.lower()
            else:
                # Fallback to old llm_provider setting
                setting = db.query(SystemSettings).filter(
                    SystemSettings.key == "llm_provider"
                ).first()
                if setting and setting.value:
                    resolved_provider = setting.value.lower()
        except Exception as e:
            # If anything goes wrong (e.g. no app context), log and fall back to config
            logger.warning(
                f"create_llm: failed to read provider from SystemSettings, "
                f"falling back to Config.LLM_PROVIDER. Error: {str(e)}"
            )

    # Final fallback to config default if still not resolved
    provider = (resolved_provider or Config.LLM_PROVIDER).lower()

    # Apply output token cap in load-test mode or when caller explicitly requests max_tokens.
    # This lets production callers raise completion length when needed without forcing caps globally.
    load_test_mode = os.getenv('LOAD_TEST_MODE', 'false').lower() in ('true', '1', 'yes')
    apply_max_tokens = bool(load_test_mode or max_tokens is not None)
    
    # Use provided values or fall back to config defaults
    if provider == 'openai':
        # OpenAI configuration
        api_key_to_use = (
            api_key or Config.OPENAI_API_KEY or _resolve_stored_api_key("openai")
        )
        if not api_key_to_use:
            raise ValueError(
                "OPENAI API key is not configured. "
                "Set OPENAI_API_KEY in .env or configure it in Admin Panel settings."
            )
        
        model = model_name or Config.OPENAI_MODEL
        temp = temperature if temperature is not None else Config.OPENAI_TEMPERATURE
        max_toks = max_tokens if max_tokens is not None else Config.OPENAI_MAX_TOKENS
        model_cap = get_openai_max_completion_tokens(model)
        if max_toks is not None and max_toks > model_cap:
            logger.warning(
                "Clamping OpenAI max_tokens from %s to %s for model %s",
                max_toks,
                model_cap,
                model,
            )
            max_toks = model_cap
        time_out = timeout if timeout is not None else Config.OPENAI_TIMEOUT
        
        openai_kwargs = {
            "openai_api_key": api_key_to_use,
            "model_name": model,
            "temperature": temp,
            "timeout": time_out,
        }
        if apply_max_tokens:
            openai_kwargs["max_tokens"] = max_toks

        llm = ChatOpenAI(**openai_kwargs)
        
        logger.info(
            "Initialized OpenAI LLM: model=%s, temperature=%s, max_tokens=%s (applied=%s)",
            model,
            temp,
            max_toks if apply_max_tokens else "provider-default",
            apply_max_tokens,
        )
        
    elif provider == 'groq':
        # Groq configuration
        if not GROQ_AVAILABLE:
            raise ValueError(
                "Groq provider requires langchain-groq package. "
                "Please install it with: pip install langchain-groq"
            )
        
        api_key_to_use = (
            api_key or os.getenv("GROQ_API_KEY", "") or _resolve_stored_api_key("groq")
        )
        if not api_key_to_use:
            raise ValueError(
                "GROQ API key is not configured. "
                "Set GROQ_API_KEY in .env or configure it in Admin Panel settings."
            )
        
        model = model_name or os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')
        temp = temperature if temperature is not None else float(os.getenv('GROQ_TEMPERATURE', '0.5'))
        max_toks = max_tokens if max_tokens is not None else int(os.getenv('GROQ_MAX_TOKENS', '1024'))
        model_cap = get_groq_max_completion_tokens(model)
        if max_toks > model_cap:
            logger.warning(
                "Clamping Groq max_tokens from %s to %s for model %s",
                max_toks,
                model_cap,
                model,
            )
            max_toks = model_cap
        time_out = timeout if timeout is not None else int(os.getenv('GROQ_TIMEOUT', '60'))
        
        groq_kwargs = {
            "groq_api_key": api_key_to_use,
            "model_name": model,
            "temperature": temp,
            "timeout": time_out,
        }
        if apply_max_tokens:
            groq_kwargs["max_tokens"] = max_toks

        # Qwen 3 on Groq emits visible "thinking" unless reasoning is disabled (Groq reasoning docs).
        # reasoning_effort=none: no reasoning tokens; reasoning_format=hidden: final answer only in content.
        # Override with GROQ_REASONING_EFFORT / GROQ_REASONING_FORMAT if needed.
        model_lower = (model or "").lower()
        if "qwen" in model_lower:
            # Groq API allows reasoning_effort "none" | "default" for Qwen; default enables planning (better tool use).
            groq_kwargs["reasoning_effort"] = os.getenv("GROQ_REASONING_EFFORT", "default")
            groq_kwargs["reasoning_format"] = os.getenv("GROQ_REASONING_FORMAT", "hidden")
        elif "gpt-oss" in model_lower:
            # GPT-OSS (the default GROQ_MODEL) also supports a reasoning_effort knob on Groq,
            # but its accepted values are "low"/"medium"/"high" - a different enum than Qwen's
            # "none"/"default", so this uses its own env var rather than sharing
            # GROQ_REASONING_EFFORT (which would silently send an invalid value to whichever
            # model family isn't currently active). Previously this branch didn't exist at all,
            # so the production default model never got an explicit reasoning_effort - it just
            # fell back to whatever Groq's own API default happens to be.
            groq_kwargs["reasoning_effort"] = os.getenv("GROQ_GPT_OSS_REASONING_EFFORT", "medium")
            groq_kwargs["reasoning_format"] = os.getenv("GROQ_REASONING_FORMAT", "hidden")

        llm = ChatGroq(**groq_kwargs)
        
        logger.info(
            "Initialized Groq LLM: model=%s, temperature=%s, max_tokens=%s (applied=%s)%s",
            model,
            temp,
            max_toks if apply_max_tokens else "provider-default",
            apply_max_tokens,
            (
                f", reasoning_effort={groq_kwargs.get('reasoning_effort')}, "
                f"reasoning_format={groq_kwargs.get('reasoning_format')}"
                if "reasoning_effort" in groq_kwargs
                else ""
            ),
        )
        
    elif provider == 'vllm':
        # vLLM configuration (OpenAI-compatible API)
        api_base = Config.VLLM_API_BASE
        model = model_name or Config.VLLM_MODEL
        temp = temperature if temperature is not None else Config.VLLM_TEMPERATURE
        max_toks = max_tokens if max_tokens is not None else Config.VLLM_MAX_TOKENS
        time_out = timeout if timeout is not None else Config.VLLM_TIMEOUT
        
        vllm_kwargs = {
            "openai_api_key": "EMPTY",  # vLLM doesn't require a real API key
            "openai_api_base": api_base,
            "model_name": model,
            "temperature": temp,
            "timeout": time_out,
        }
        if apply_max_tokens:
            vllm_kwargs["max_tokens"] = max_toks

        llm = ChatOpenAI(**vllm_kwargs)
        
        logger.info(
            "Initialized vLLM LLM: base=%s, model=%s, temperature=%s, max_tokens=%s (applied=%s)",
            api_base,
            model,
            temp,
            max_toks if apply_max_tokens else "provider-default",
            apply_max_tokens,
        )
        
    else:
        raise ValueError(
            f"Invalid LLM_PROVIDER: '{provider}'. Must be 'openai', 'groq', or 'vllm'. "
            f"Current value: {provider or Config.LLM_PROVIDER}"
        )

    return wrap_llm_with_telemetry(llm, provider=provider, model_name=str(model))

