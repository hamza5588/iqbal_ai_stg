import logging
import os
import re
import tempfile
import unicodedata
import wave
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from faster_whisper.audio import decode_audio
from langdetect import detect

from app.utils.faster_whisper_engine import transcribe_with_faster_whisper
from piper import PiperVoice
from gtts import gTTS
try:
    import webrtcvad  # type: ignore
except Exception:  # pragma: no cover
    webrtcvad = None

logger = logging.getLogger(__name__)

_PIPER_VOICES: Dict[str, PiperVoice] = {}

SUPPORTED_STT_LANGS = {"auto", "en", "ur", "hi"}
SUPPORTED_TTS_LANGS = {"en", "ur", "hi", "ur-Latn"}
STT_SAMPLE_RATE = 16000
VAD_FRAME_MS = 30
VAD_MODE = 3  # most aggressive noise rejection


def normalize_language(lang: Optional[str], default: str = "auto") -> str:
    if not lang:
        return default
    normalized = str(lang).strip().lower()
    aliases = {
        "auto-detect": "auto",
        "english": "en",
        "urdu": "ur",
        "roman-urdu": "ur-Latn",
        "roman_urdu": "ur-Latn",
        "roman urdu": "ur-Latn",
        "hindi": "hi",
    }
    return aliases.get(normalized, normalized)


def clean_text_for_tts(text: str) -> str:
    source = unicodedata.normalize("NFC", text or "")

    # Remove fenced/inline code and markdown artifacts.
    source = re.sub(r"```[\s\S]*?```", " ", source)
    source = re.sub(r"`[^`]*`", " ", source)
    source = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1", source)
    source = re.sub(r"https?://\S+", " ", source)
    source = re.sub(r"www\.\S+", " ", source)

    # Remove most emoji/pictographic symbols (they sound unnatural in TTS).
    source = re.sub(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]+", " ", source)

    # Remove noisy markdown/control symbols.
    source = re.sub(r"[#*_~`>|=\[\]{}^\\]+", " ", source)
    source = re.sub(r"\s*[-]{2,}\s*", ". ", source)
    source = re.sub(r"\s*[•●▪◦]\s*", ". ", source)

    # Keep letters/numbers/basic punctuation commonly pronounced well by TTS engines.
    source = re.sub(r"[^\w\s\.,!\?:;%\-\u0600-\u06FF]", " ", source, flags=re.UNICODE)
    source = re.sub(r"\s+", " ", source)
    source = re.sub(r"\s+([\.,!\?:;%])", r"\1", source)
    return source.strip()


def _is_urdu_script(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def detect_tts_language(text: str, lang_hint: Optional[str] = None) -> str:
    hint = normalize_language(lang_hint, default="auto")
    if hint != "auto":
        if hint == "ur-latn":
            return "ur-Latn"
        return hint if hint in SUPPORTED_TTS_LANGS else "en"

    if _is_urdu_script(text):
        return "ur"

    try:
        detected = detect(text)
        if detected in SUPPORTED_TTS_LANGS:
            return detected
    except Exception:
        pass

    # Roman Urdu often gets detected as English; keep explicit separate option.
    return "en"


def _float32_to_pcm16_bytes(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    int16 = (clipped * 32767.0).astype(np.int16)
    return int16.tobytes()


def _run_vad_gate(audio_path: str) -> Tuple[bool, dict]:
    """
    Return whether this audio likely contains human speech.
    Uses WebRTC VAD (mode=2) with conservative minimum speech constraints.
    """
    try:
        audio = decode_audio(audio_path, sampling_rate=STT_SAMPLE_RATE)
    except Exception as exc:
        logger.warning("VAD precheck load failed, skipping strict gate: %s", exc)
        return True, {"vad_skipped": True}

    duration_sec = float(len(audio)) / float(STT_SAMPLE_RATE) if len(audio) else 0.0
    if duration_sec < 0.35:
        return False, {"duration_sec": round(duration_sec, 3), "reason": "too_short"}

    # Basic energy gate first to reject near-silence clips quickly.
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    if rms < 0.006:
        return False, {"duration_sec": round(duration_sec, 3), "rms": round(rms, 5), "reason": "low_energy"}

    if webrtcvad is None:
        # Keep app functional even if py-webrtcvad dependencies are missing.
        return True, {
            "duration_sec": round(duration_sec, 3),
            "rms": round(rms, 5),
            "vad_skipped": True,
            "reason": "webrtcvad_unavailable",
        }

    vad = webrtcvad.Vad(VAD_MODE)
    frame_samples = int(STT_SAMPLE_RATE * (VAD_FRAME_MS / 1000.0))
    frame_bytes = frame_samples * 2
    pcm = _float32_to_pcm16_bytes(audio)

    speech_frames = 0
    total_frames = 0
    max_consecutive = 0
    consecutive = 0

    for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        frame = pcm[i:i + frame_bytes]
        total_frames += 1
        is_speech = vad.is_speech(frame, STT_SAMPLE_RATE)
        if is_speech:
            speech_frames += 1
            consecutive += 1
            if consecutive > max_consecutive:
                max_consecutive = consecutive
        else:
            consecutive = 0

    if total_frames == 0:
        return False, {"duration_sec": round(duration_sec, 3), "reason": "no_frames"}

    speech_ratio = speech_frames / total_frames
    max_speech_ms = max_consecutive * VAD_FRAME_MS
    # Stricter gate to ignore faint environment voices/noise.
    has_speech = speech_ratio >= 0.20 and max_speech_ms >= 300 and rms >= 0.009

    return has_speech, {
        "duration_sec": round(duration_sec, 3),
        "rms": round(rms, 5),
        "speech_ratio": round(speech_ratio, 3),
        "max_speech_ms": int(max_speech_ms),
    }


def transcribe_audio_file(audio_path: str, language: Optional[str] = None) -> Tuple[Optional[dict], Optional[dict]]:
    if not audio_path or not os.path.exists(audio_path):
        return None, {"code": "AUDIO_MISSING", "message": "Audio file not found"}

    if os.path.getsize(audio_path) < 4096:
        return None, {"code": "AUDIO_TOO_SHORT", "message": "Audio is too short to transcribe"}

    lang = normalize_language(language, default="auto")
    whisper_lang = None if lang == "auto" else ("ur" if lang == "ur-latn" else lang)
    if whisper_lang and whisper_lang not in SUPPORTED_STT_LANGS:
        whisper_lang = None

    has_speech, vad_meta = _run_vad_gate(audio_path)
    if not has_speech:
        return None, {
            "code": "NO_SPEECH",
            "message": "No clear voice detected. Please reduce background noise and speak clearly.",
            "meta": vad_meta,
        }

    result, stt_err = transcribe_with_faster_whisper(audio_path, language=whisper_lang)
    if stt_err:
        return None, stt_err

    text = (result.get("text") or "").strip()
    avg_no_speech = float(result.get("avg_no_speech_prob") or 0.0)

    if not text or (avg_no_speech > 0.72 and len(text) < 3):
        return None, {"code": "NO_SPEECH", "message": "No clear speech detected. Please speak closer to the mic."}

    return {
        "text": text,
        "language": result.get("language") or whisper_lang or "unknown",
        "avg_no_speech_prob": round(avg_no_speech, 3),
        "vad": vad_meta,
    }, None


def _voice_model_path_for_lang(lang: str) -> Optional[Path]:
    # Default filenames match rhasspy/piper-voices on Hugging Face (see docs/piper_models_setup.md).
    configured = {
        "en": os.getenv("PIPER_EN_MODEL_PATH", "piper_models/en_US-lessac-medium.onnx"),
        "ur": os.getenv("PIPER_UR_MODEL_PATH", "piper_models/ur_PK-fasih-medium.onnx"),
        "hi": os.getenv("PIPER_HI_MODEL_PATH", "piper_models/hi_IN-rohan-medium.onnx"),
        "ur-Latn": os.getenv("PIPER_UR_MODEL_PATH", "piper_models/ur_PK-fasih-medium.onnx"),
    }
    model_path = configured.get(lang)
    if not model_path:
        return None
    path = Path(model_path)
    if not path.is_absolute():
        app_root = Path(__file__).resolve().parents[2]
        path = app_root / model_path
    return path


def warmup_piper_voice_languages(langs: Optional[List[str]] = None) -> None:
    """
    Preload Piper ONNX voices into memory for this process (optional HTTP worker startup).

    Missing models are skipped with a warning (same as first TTS request).
    """
    want = langs or ["en"]
    for lang in want:
        try:
            _get_piper_voice(lang)
            logger.info("Piper voice preloaded for language '%s'", lang)
        except Exception as exc:
            logger.warning("Piper preload skipped for '%s': %s", lang, exc)


def _get_piper_voice(lang: str) -> PiperVoice:
    if lang in _PIPER_VOICES:
        return _PIPER_VOICES[lang]

    model_path = _voice_model_path_for_lang(lang)
    if not model_path or not model_path.exists():
        raise FileNotFoundError(f"Piper model missing for '{lang}'. Configure model path in environment.")

    voice = PiperVoice.load(model_path=str(model_path), use_cuda=False)
    _PIPER_VOICES[lang] = voice
    return voice


def _gtts_language_for(lang: str) -> str:
    # gTTS uses language codes like en/ur/hi.
    mapping = {
        "en": "en",
        "ur": "ur",
        "hi": "hi",
        "ur-Latn": "ur",
    }
    return mapping.get(lang, "en")


def _synthesize_gtts_mp3(text: str, language: str) -> Tuple[Optional[BytesIO], Optional[dict]]:
    try:
        tts = gTTS(text=text, lang=_gtts_language_for(language))
        audio_fp = BytesIO()
        tts.write_to_fp(audio_fp)
        audio_fp.seek(0)
        return audio_fp, {"language": language, "format": "mp3", "engine": "gtts"}
    except Exception as exc:
        logger.exception("gTTS synthesis failed for '%s': %s", language, exc)
        return None, {"code": "TTS_FAILED", "message": "Failed to generate speech audio"}


def synthesize_text_to_wav(text: str, lang_hint: Optional[str] = None) -> Tuple[Optional[BytesIO], Optional[dict]]:
    cleaned = clean_text_for_tts(text)
    if not cleaned:
        return None, {"code": "TEXT_EMPTY", "message": "Text is required"}

    language = detect_tts_language(cleaned, lang_hint)
    try:
        voice = _get_piper_voice(language)
    except FileNotFoundError as exc:
        logger.warning("Piper model not found for language '%s': %s", language, exc)
        # Smart fallback: keep multilingual functionality even without local Piper voices.
        fallback_audio, fallback_meta = _synthesize_gtts_mp3(cleaned, language)
        if fallback_audio is not None:
            return fallback_audio, fallback_meta
        return None, {
            "code": "VOICE_MODEL_MISSING",
            "message": f"Voice model for '{language}' is not installed on server",
            "language": language,
        }
    except Exception as exc:
        logger.exception("Failed loading Piper voice for '%s': %s", language, exc)
        return None, {"code": "TTS_ENGINE_ERROR", "message": "Failed to initialize TTS engine"}

    try:
        audio_fp = BytesIO()
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            with wave.open(tmp.name, "wb") as wav_file:
                voice.synthesize_wav(cleaned, wav_file)
            tmp.seek(0)
            audio_fp.write(tmp.read())
        audio_fp.seek(0)
        return audio_fp, {"language": language, "format": "wav", "engine": "piper"}
    except Exception as exc:
        logger.exception("Piper synthesis failed: %s", exc)
        return None, {"code": "TTS_FAILED", "message": "Failed to generate speech audio"}
