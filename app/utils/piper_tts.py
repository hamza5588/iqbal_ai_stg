"""
Local text-to-speech using piper-tts.

Configurable via environment variables:
    PIPER_VOICE      — Voice name (default: en_US-lessac-medium)
                       Format: {lang}_{REGION}-{name}-{quality}
                       Examples: en_US-lessac-medium, en_GB-alba-medium,
                                 en_US-amy-low, en_US-ryan-high
    PIPER_MODELS_DIR — Directory for cached voice models (default: <project>/piper_models)

Available English voices (see https://rhasspy.github.io/piper-samples/):
    en_US-lessac-medium  — US female, clear and natural (recommended)
    en_US-lessac-high    — US female, higher quality, slower
    en_US-amy-medium     — US female, warm tone
    en_US-ryan-medium    — US male, neutral
    en_US-ryan-high      — US male, higher quality
    en_GB-alba-medium    — British female
    en_GB-aru-medium     — British male
    en_AU-karen-medium   — Australian female

Voice models (~50-100MB) are auto-downloaded from HuggingFace on first use.
Requires espeak-ng system package: apt install espeak-ng (Linux) or brew install espeak-ng (macOS).
"""
import io
import logging
import os
import wave
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_MODELS_DIR = os.environ.get("PIPER_MODELS_DIR", "").strip() or os.path.join(_PROJECT_ROOT, "piper_models")
_DEFAULT_VOICE = os.environ.get("PIPER_VOICE", "en_US-lessac-medium")

_piper_voice = None
_piper_available = None


def _voice_urls(voice_name: str):
    """Build HuggingFace download URLs for a piper voice.

    Repo layout: /{lang}/{lang_REGION}/{name}/{quality}/{voice_name}.onnx
    e.g. en_US-lessac-medium → /en/en_US/lessac/medium/en_US-lessac-medium.onnx
    """
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    # voice_name format: {lang_REGION}-{name}-{quality}
    parts = voice_name.split("-")        # ['en_US', 'lessac', 'medium']
    lang_region = parts[0]               # en_US
    lang_code = lang_region.split("_")[0]  # en
    name = parts[1] if len(parts) > 1 else ""
    quality = parts[2] if len(parts) > 2 else ""
    path = f"{base}/{lang_code}/{lang_region}/{name}/{quality}"
    return {
        "model": f"{path}/{voice_name}.onnx",
        "config": f"{path}/{voice_name}.onnx.json",
    }


def _download_file(url: str, dest: str):
    """Download a file if it doesn't already exist."""
    if os.path.isfile(dest):
        return
    import urllib.request
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    logger.info("Downloading piper voice file: %s", url)
    urllib.request.urlretrieve(url, dest)
    logger.info("Downloaded: %s", dest)


def _load_piper_voice():
    """Lazy-load the piper voice model, downloading if necessary."""
    global _piper_voice, _piper_available
    if _piper_available is False:
        return None
    if _piper_voice is not None:
        return _piper_voice
    try:
        from piper import PiperVoice

        voice_name = _DEFAULT_VOICE
        urls = _voice_urls(voice_name)
        model_path = os.path.join(_MODELS_DIR, f"{voice_name}.onnx")
        config_path = os.path.join(_MODELS_DIR, f"{voice_name}.onnx.json")

        _download_file(urls["model"], model_path)
        _download_file(urls["config"], config_path)

        _piper_voice = PiperVoice.load(model_path, config_path=config_path)
        _piper_available = True
        logger.info("Loaded piper voice: %s", voice_name)
        return _piper_voice
    except ImportError as e:
        logger.warning("piper-tts not installed: %s. Install with: pip install piper-tts", e)
        _piper_available = False
        return None
    except Exception as e:
        logger.exception("Failed to load piper voice: %s", e)
        _piper_available = False
        return None


def synthesize_speech(text: str) -> Optional[io.BytesIO]:
    """
    Synthesize text to WAV audio using piper-tts.

    :param text: Text to synthesize.
    :return: BytesIO containing WAV audio, or None on failure.
    """
    voice = _load_piper_voice()
    if voice is None:
        return None
    try:
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        wav_buffer.seek(0)
        return wav_buffer
    except Exception as e:
        logger.exception("Piper synthesis failed: %s", e)
        return None
