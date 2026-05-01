"""
Local speech-to-text via faster-whisper (CTranslate2), typically int8 on CPU.

Configure with environment variables:
  WHISPER_MODEL        — tiny, small, base, etc. (default: small)
  WHISPER_DEVICE       — cpu or cuda (default: cpu)
  WHISPER_COMPUTE_TYPE — int8, int8_float16, float16, float32 (default: int8 on cpu)
  WHISPER_BEAM_SIZE    — 1 = fastest (default: 1)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_model = None
_model_load_error: Optional[str] = None

WHISPER_MODEL_SIZE = (os.getenv("WHISPER_MODEL", "small") or "small").strip()
WHISPER_DEVICE = (os.getenv("WHISPER_DEVICE", "cpu") or "cpu").strip().lower()
_default_compute = "int8" if WHISPER_DEVICE == "cpu" else "float16"
WHISPER_COMPUTE_TYPE = (os.getenv("WHISPER_COMPUTE_TYPE") or _default_compute).strip()
try:
    WHISPER_BEAM_SIZE = max(1, int(os.getenv("WHISPER_BEAM_SIZE", "1")))
except ValueError:
    WHISPER_BEAM_SIZE = 1


def get_whisper_model():
    """Lazy-load faster-whisper model (singleton)."""
    global _model, _model_load_error
    if _model_load_error is not None:
        return None
    if _model is not None:
        return _model
    try:
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
        logger.info(
            "Loaded faster-whisper model=%s device=%s compute_type=%s beam_size=%s",
            WHISPER_MODEL_SIZE,
            WHISPER_DEVICE,
            WHISPER_COMPUTE_TYPE,
            WHISPER_BEAM_SIZE,
        )
        return _model
    except Exception as e:
        _model_load_error = str(e)
        logger.exception("Failed to load faster-whisper: %s", e)
        return None


def transcribe_with_faster_whisper(
    audio_path: str,
    language: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
    """
    Transcribe a file path. Returns (result_dict, None) on success.
    result_dict keys: text, language, avg_no_speech_prob, segments (list of dicts with no_speech_prob).
    """
    model = get_whisper_model()
    if model is None:
        return None, {
            "code": "STT_FAILED",
            "message": "Speech recognition model is not available. Check server logs.",
        }

    kwargs: Dict[str, Any] = {
        "beam_size": WHISPER_BEAM_SIZE,
        "vad_filter": False,
    }
    if language:
        kwargs["language"] = language

    try:
        segments_gen, info = model.transcribe(audio_path, **kwargs)
    except Exception as exc:
        logger.exception("faster-whisper transcribe failed: %s", exc)
        return None, {"code": "STT_FAILED", "message": "Failed to transcribe audio"}

    seg_dicts: List[Dict[str, Any]] = []
    texts: List[str] = []
    for seg in segments_gen:
        texts.append(seg.text or "")
        seg_dicts.append({"no_speech_prob": float(getattr(seg, "no_speech_prob", 0.0) or 0.0)})

    text = "".join(texts).strip()
    avg_no_speech = 0.0
    if seg_dicts:
        avg_no_speech = sum(s["no_speech_prob"] for s in seg_dicts) / len(seg_dicts)

    out_lang = getattr(info, "language", None) or language or "unknown"

    return {
        "text": text,
        "language": out_lang,
        "avg_no_speech_prob": avg_no_speech,
        "segments": seg_dicts,
    }, None
