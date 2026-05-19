"""
OpenAI Realtime API (GA) helpers — server-side WebRTC proxy.

/voice/call flow (server-proxied, GA models):

  Step 1:  POST /v1/realtime/client_secrets  (API key + session with gpt-realtime)
  Step 2:  POST /v1/realtime/calls  (ephemeral Bearer + raw SDP body)

  Do NOT use ?model= on /calls or Python multipart (OpenAI returned SDP EOF with both).

The browser sends its SDP offer to *our* server; we exchange it with OpenAI
and return the SDP answer so the browser can call pc.setRemoteDescription().

GA models (beta gpt-4o-realtime-preview-* is deprecated):
  - gpt-realtime      — GA speech-to-speech (default for consultant voice)
  - gpt-realtime-2    — GA reasoning voice (set OPENAI_REALTIME_MODEL=gpt-realtime-2)

Session config (client_secrets wrapper):

  {
    "type": "realtime",
    "model": "gpt-realtime",
    "instructions": "...",
    "audio": {
      "input":  { "transcription": {...}, "turn_detection": {...} },
      "output": { "voice": "marin" }
    }
  }
"""

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

OPENAI_REALTIME_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
OPENAI_REALTIME_CALLS_URL         = "https://api.openai.com/v1/realtime/calls"

# GA realtime models — https://developers.openai.com/api/docs/models/gpt-realtime
DEFAULT_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
DEFAULT_REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "marin")
DEFAULT_REALTIME_REASONING_EFFORT = os.getenv(
    "OPENAI_REALTIME_REASONING_EFFORT", "low"
)

_VALID_REASONING_EFFORTS = frozenset(
    {"minimal", "low", "medium", "high", "xhigh"}
)


def _resolved_model(model: str | None) -> str:
    return (model or DEFAULT_REALTIME_MODEL).strip()


def _model_supports_reasoning(model: str) -> bool:
    """gpt-realtime-2 supports reasoning.effort; gpt-realtime does not."""
    return model == "gpt-realtime-2" or model.startswith("gpt-realtime-2-")


# ---------------------------------------------------------------------------
# Session config helpers
# ---------------------------------------------------------------------------

def build_realtime_session_config(
    instructions: str,
    *,
    model: str | None = None,
    voice: str | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    """
    Session config for GA Realtime (multipart /calls or /client_secrets).

    - type "realtime" is required
    - voice lives at audio.output.voice (not top-level)
    - gpt-realtime-2 only: reasoning.effort (via OPENAI_REALTIME_REASONING_EFFORT)
    """
    resolved_model = _resolved_model(model)
    config: dict = {
        "type":         "realtime",
        "model":        resolved_model,
        "instructions": instructions,
        "audio": {
            "input": {
                "transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type":                 "server_vad",
                    "threshold":            0.5,
                    "prefix_padding_ms":    300,
                    "silence_duration_ms":  600,
                },
            },
            "output": {
                "voice": voice or DEFAULT_REALTIME_VOICE,
            },
        },
    }
    if _model_supports_reasoning(resolved_model):
        effort = (reasoning_effort or DEFAULT_REALTIME_REASONING_EFFORT or "").strip()
        if effort in _VALID_REASONING_EFFORTS:
            config["reasoning"] = {"effort": effort}
    return config


def build_realtime_session_payload(
    instructions: str,
    *,
    model: str | None = None,
    voice: str | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    """Wrapped payload for POST /v1/realtime/client_secrets (requires 'session' key)."""
    return {
        "session": build_realtime_session_config(
            instructions,
            model=model,
            voice=voice,
            reasoning_effort=reasoning_effort,
        ),
    }


# ---------------------------------------------------------------------------
# Step 1 – mint ephemeral token
# ---------------------------------------------------------------------------

def create_realtime_client_secret(
    api_key: str,
    instructions: str,
    *,
    model: str | None = None,
    voice: str | None = None,
    safety_identifier: str | None = None,
    timeout: int = 30,
) -> requests.Response:
    """
    POST /v1/realtime/client_secrets with the server API key.

    Successful response JSON:
        {
          "client_secret": { "value": "ek_...", "expires_at": 1234567890 },
          "session":        { "id": "sess_...", "model": "...", ... }
        }
    Extract the ephemeral key with: resp.json()["client_secret"]["value"]
    """
    payload = build_realtime_session_payload(instructions, model=model, voice=voice)
    logger.info(
        "Creating realtime client_secret: model=%s voice=%s instructions_len=%s",
        payload["session"].get("model"),
        payload["session"].get("audio", {}).get("output", {}).get("voice"),
        len(instructions),
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    if safety_identifier:
        headers["OpenAI-Safety-Identifier"] = safety_identifier

    return requests.post(
        OPENAI_REALTIME_CLIENT_SECRETS_URL,
        headers=headers,
        json=payload,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Unified SDP exchange (server-proxied /voice/call)
# ---------------------------------------------------------------------------

def _extract_ephemeral_key(session_data: dict) -> str | None:
    """GA client_secrets returns top-level value; some clients use client_secret.value."""
    key = session_data.get("value")
    if key:
        return str(key)
    nested = session_data.get("client_secret")
    if isinstance(nested, dict) and nested.get("value"):
        return str(nested["value"])
    return None


def exchange_realtime_call(
    api_key: str,
    sdp: str,
    instructions: str,
    *,
    model: str | None = None,
    voice: str | None = None,
    safety_identifier: str | None = None,
    timeout: int = 30,
    max_retries: int = 3,
) -> requests.Response:
    """
    Server-side WebRTC SDP exchange (GA, matches OpenAI WebRTC guide).

    1. POST /v1/realtime/client_secrets — session uses gpt-realtime (or env override)
    2. POST /v1/realtime/calls — ephemeral Bearer + raw SDP (application/sdp)

    Do NOT append ?model= or use multipart here; that caused SDP EOF parse errors.
    """
    if not sdp or "v=0" not in sdp:
        raise ValueError("Invalid or empty WebRTC SDP offer")

    sdp_bytes = sdp.encode("utf-8")
    resolved_model = _resolved_model(model)
    logger.info(
        "exchange_realtime_call: model=%s sdp_bytes=%s",
        resolved_model,
        len(sdp_bytes),
    )

    session_resp = create_realtime_client_secret(
        api_key,
        instructions,
        model=resolved_model,
        voice=voice,
        safety_identifier=safety_identifier,
        timeout=timeout,
    )
    if session_resp.status_code != 200:
        logger.error(
            "OpenAI realtime/client_secrets failed: %s %s",
            session_resp.status_code,
            session_resp.text[:500],
        )
        return session_resp

    session_data = session_resp.json()
    ephemeral_key = _extract_ephemeral_key(session_data)
    if not ephemeral_key:
        raise ValueError(
            f"No ephemeral key in client_secrets response — keys: {list(session_data)}"
        )

    sess = session_data.get("session") or {}
    logger.info(
        "Got ephemeral key model=%s prefix=%s…",
        sess.get("model", resolved_model),
        ephemeral_key[:8],
    )

    call_headers = {
        "Authorization": f"Bearer {ephemeral_key}",
        "Content-Type":  "application/sdp",
        "Accept":        "application/sdp",
    }

    last_resp: requests.Response | None = None
    for attempt in range(max_retries):
        logger.info(
            "POST %s  attempt=%s  sdp_bytes=%s",
            OPENAI_REALTIME_CALLS_URL,
            attempt + 1,
            len(sdp_bytes),
        )
        last_resp = requests.post(
            OPENAI_REALTIME_CALLS_URL,
            data=sdp_bytes,
            headers=call_headers,
            timeout=timeout,
        )
        logger.info(
            "OpenAI realtime/calls response: status=%s content_type=%s body_prefix=%r",
            last_resp.status_code,
            last_resp.headers.get("content-type", ""),
            last_resp.text[:120],
        )
        if last_resp.status_code != 429 or attempt >= max_retries - 1:
            return last_resp
        # Billing quota — retrying will not help
        if is_openai_quota_error(last_resp):
            return last_resp
        retry_after = last_resp.headers.get("retry-after")
        try:
            delay = float(retry_after) if retry_after else 2 ** attempt
        except ValueError:
            delay = 2 ** attempt
        time.sleep(min(delay, 30))

    assert last_resp is not None
    return last_resp


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def parse_retry_after_seconds(resp: requests.Response) -> int:
    """Seconds to wait before retrying after a 429."""
    raw = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
    if not raw:
        return 60
    try:
        return max(1, min(int(float(raw)), 120))
    except (ValueError, TypeError):
        return 60


def is_realtime_sdp_success(resp: requests.Response) -> bool:
    """OpenAI /realtime/calls returns SDP answer with 200 or 201."""
    if resp.status_code not in (200, 201):
        return False
    return (resp.text or "").lstrip().startswith("v=0")


def is_openai_quota_error(resp: requests.Response) -> bool:
    """True when 429 is billing/quota exhausted (not a transient rate limit)."""
    if resp.status_code != 429:
        return False
    msg = openai_error_message(resp).lower()
    return (
        "exceeded your current quota" in msg
        or "insufficient_quota" in msg
        or ("quota" in msg and "billing" in msg)
    )


def openai_error_message(resp: requests.Response) -> str:
    """Best-effort parse of OpenAI error JSON for user-facing messages."""
    try:
        body = resp.json()
        err  = body.get("error") or body
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    except (ValueError, AttributeError):
        pass
    text = (resp.text or "").strip()
    return text[:300] if text else f"OpenAI request failed ({resp.status_code})"


def parse_client_secret_response(data: dict) -> dict:
    """
    Normalise the /v1/realtime/client_secrets JSON for the /voice/session route.

    Response shape:
        {
          "client_secret": { "value": "ek_...", "expires_at": 1234567890 },
          "session":        { "id": "sess_...", "model": "...", ... }
        }
    """
    # Actual response shape from /v1/realtime/client_secrets:
    # {"value": "ek_...", "expires_at": ..., "session": {"id": "...", "model": "...", ...}}
    sess   = data.get("session") or {}
    audio  = sess.get("audio") or {}
    output = audio.get("output") or {}
    return {
        "client_secret": {
            "value":      data.get("value"),
            "expires_at": data.get("expires_at"),
        },
        "session_id": sess.get("id"),
        "model":      sess.get("model"),
        "voice":      output.get("voice"),
    }
