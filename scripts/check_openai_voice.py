#!/usr/bin/env python3
"""Check OpenAI Realtime API key without printing the key."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))


def main():
    from app import create_app
    from app.routes.consultant_routes import _get_openai_key, _openai_key_source, _realtime_model

    app = create_app()
    with app.app_context():
        key = _get_openai_key(for_realtime=True)
        source = _openai_key_source(for_realtime=True)
        model = _realtime_model()

    if not key:
        print("FAIL: No OpenAI API key (check .env OPENAI_API_KEY or Admin settings)")
        return 1

    print(f"Key source: {source}")
    print(f"Realtime model: {model}")

    r = requests.post(
        "https://api.openai.com/v1/realtime/client_secrets",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"session": {"type": "realtime", "model": model}},
        timeout=20,
    )

    if r.status_code == 200:
        print("OK: API key is valid and Realtime client_secrets works")
        return 0

    print(f"FAIL: OpenAI returned HTTP {r.status_code}")
    print(r.text[:400])
    if r.status_code == 401:
        print("→ Key is invalid or expired. Update OPENAI_API_KEY in .env")
    elif r.status_code == 429:
        print("→ Key is valid but rate-limited. Wait 1–2 minutes and retry.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
