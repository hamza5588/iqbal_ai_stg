"""
Local speech-to-text using faster-whisper (CTranslate2-optimized Whisper).

Configurable via environment variables:
    WHISPER_MODEL_SIZE  — Model size: tiny, base, small, medium, large-v2 (default: base)
    WHISPER_DEVICE      — Device: cpu or cuda (default: cpu)
    WHISPER_COMPUTE_TYPE — Quantization: int8, float16, float32 (default: int8)
    WHISPER_LANGUAGE    — Language code: en, es, fr, etc. (default: en). Auto-detect if empty.

On macOS, thread counts are limited to 1 to prevent POSIX semaphore leaks
from CTranslate2. On Linux/Docker this restriction is not applied.
"""
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration (ENV) ──
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "en").strip() or None

_whisper_model = None
_whisper_available = None


def _load_whisper_model():
    """Lazy-load the faster-whisper model once."""
    global _whisper_model, _whisper_available
    if _whisper_available is False:
        return None
    if _whisper_model is not None:
        return _whisper_model
    try:
        from faster_whisper import WhisperModel

        # macOS: limit threads to prevent POSIX semaphore leaks from CTranslate2
        kwargs = {}
        if sys.platform == "darwin":
            os.environ.setdefault("OMP_NUM_THREADS", "1")
            kwargs = {"cpu_threads": 1, "num_workers": 1}

        _whisper_model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
            **kwargs,
        )
        _whisper_available = True
        logger.info(
            "Loaded faster-whisper model: %s (device=%s, compute=%s, lang=%s)",
            WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE,
            WHISPER_LANGUAGE or "auto",
        )
        return _whisper_model
    except ImportError as e:
        logger.warning("faster-whisper not installed: %s. Install with: pip install faster-whisper", e)
        _whisper_available = False
        return None
    except Exception as e:
        logger.exception("Failed to load faster-whisper model: %s", e)
        _whisper_available = False
        return None


def transcribe_audio(audio_path: str, language: Optional[str] = None) -> Optional[str]:
    """
    Transcribe an audio file using the local faster-whisper model.

    :param audio_path: Path to the audio file (e.g. .webm, .mp3, .wav).
    :param language: Language code override. Uses WHISPER_LANGUAGE env var if None.
    :return: Transcribed text, or None on failure.
    """
    if not audio_path or not os.path.isfile(audio_path):
        return None
    model = _load_whisper_model()
    if model is None:
        return None
    try:
        lang = language or WHISPER_LANGUAGE
        kwargs = {}
        if lang:
            kwargs["language"] = lang

        segments, _info = model.transcribe(audio_path, **kwargs)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text if text else None
    except Exception as e:
        logger.exception("Whisper transcription failed for %s: %s", audio_path, e)
        return None
