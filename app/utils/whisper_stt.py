"""
Local Whisper speech-to-text using the openai-whisper package (base model).
Runs on CPU only for reliable use on Ubuntu servers without GPU/CUDA.
Used by /api/stt and RAG chat audio to avoid OpenAI API for transcription.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Force CPU so transcription works on Ubuntu servers without GPU and avoids CUDA errors
WHISPER_DEVICE = "cpu"
WHISPER_MODEL = "base"

_whisper_model = None
_whisper_available = None


def _load_whisper_model():
    """Lazy-load the Whisper base model once, on CPU only (server-safe)."""
    global _whisper_model, _whisper_available
    if _whisper_available is False:
        return None
    if _whisper_model is not None:
        return _whisper_model
    try:
        import whisper
        _whisper_model = whisper.load_model(WHISPER_MODEL, device=WHISPER_DEVICE)
        _whisper_available = True
        logger.info("Loaded local Whisper model: %s (device=%s)", WHISPER_MODEL, WHISPER_DEVICE)
        return _whisper_model
    except ImportError as e:
        logger.warning("openai-whisper not installed: %s. Install with: pip install openai-whisper", e)
        _whisper_available = False
        return None
    except Exception as e:
        logger.exception("Failed to load Whisper model: %s", e)
        _whisper_available = False
        return None


def transcribe_audio(audio_path: str, language: Optional[str] = None) -> Optional[str]:
    """
    Transcribe an audio file using the local Whisper base model (CPU only).

    :param audio_path: Path to the audio file (e.g. .webm, .mp3, .wav).
    :param language: Optional language code (e.g. "en"). Auto-detected if None.
    :return: Transcribed text, or None on failure.
    """
    if not audio_path or not os.path.isfile(audio_path):
        return None
    model = _load_whisper_model()
    if model is None:
        return None
    try:
        kwargs = {"verbose": False, "fp16": False}
        if language:
            kwargs["language"] = language
        result = model.transcribe(audio_path, **kwargs)
        text = (result.get("text") or "").strip()
        return text if text else None
    except Exception as e:
        logger.exception("Whisper transcription failed for %s: %s", audio_path, e)
        return None
