"""
Local speech-to-text via faster-whisper (CTranslate2), shared with voice_service.

See app.utils.faster_whisper_engine for WHISPER_MODEL, WHISPER_COMPUTE_TYPE, etc.
"""
import logging
import os
from typing import Optional

from app.utils.faster_whisper_engine import transcribe_with_faster_whisper

logger = logging.getLogger(__name__)


def transcribe_audio(audio_path: str, language: Optional[str] = None) -> Optional[str]:
    """
    Transcribe an audio file using the configured faster-whisper model.

    :param audio_path: Path to the audio file (e.g. .webm, .mp3, .wav).
    :param language: Optional language code (e.g. "en"). Auto-detected if None.
    :return: Transcribed text, or None on failure.
    """
    if not audio_path or not os.path.isfile(audio_path):
        return None
    result, err = transcribe_with_faster_whisper(audio_path, language=language)
    if err or not result:
        return None
    text = (result.get("text") or "").strip()
    return text if text else None
