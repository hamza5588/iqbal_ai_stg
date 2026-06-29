"""Authentication and rate limiting for public embed APIs."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock
from typing import Optional

from flask import request

from app.config import Config
from app.models.database_models import EmbedClient
from app.services.embed_service import find_client_by_secret, get_client_by_slug

logger = logging.getLogger(__name__)

_RATE_LOCK = Lock()
_RATE_BUCKETS: dict[str, list[float]] = defaultdict(list)


def _parse_env_client_keys() -> dict[str, str]:
    raw = (Config.EMBED_CLIENT_KEYS or "").strip()
    if not raw:
        return {}
    result: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        client_id, secret = part.split(":", 1)
        if client_id.strip() and secret.strip():
            result[client_id.strip()] = secret.strip()
    return result


def get_first_client_key() -> tuple[str, str] | None:
    keys = _parse_env_client_keys()
    if not keys:
        return None
    slug = next(iter(keys))
    return slug, keys[slug]


def resolve_client_from_secret(secret: str) -> Optional[EmbedClient]:
    if not secret:
        return None
    client = find_client_by_secret(secret)
    if client:
        return client
    for slug, env_secret in _parse_env_client_keys().items():
        if env_secret == secret:
            return get_client_by_slug(slug)
    return None


def get_client_key_from_request() -> str:
    header = request.headers.get("X-Client-Key", "")
    if header:
        return header.strip()
    data = request.get_json(force=True, silent=True) or {}
    return (data.get("client_key") or data.get("clientKey") or "").strip()


def is_origin_allowed_for_client(origin: str, client: EmbedClient) -> bool:
    if not origin:
        return True

    # Local dev: allow any localhost / 127.0.0.1 port (demo page, dev servers)
    if str(Config.ENV).lower() == "local":
        if origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:"):
            return True

    from app.services.embed_service import parse_allowed_origins

    allowed = set(parse_allowed_origins(client.allowed_origins)) | set(Config.ALLOWED_ORIGINS)
    allowed = {o for o in allowed if o}
    if not allowed:
        return Config.ENV == "local"
    return origin in allowed


def check_rate_limit(bucket_key: str) -> tuple[bool, int]:
    limit = int(Config.EMBED_RATE_LIMIT_PER_HOUR or 60)
    window = 3600.0
    now = time.time()
    with _RATE_LOCK:
        hits = _RATE_BUCKETS[bucket_key]
        hits[:] = [t for t in hits if now - t < window]
        if len(hits) >= limit:
            retry_after = max(1, int(window - (now - hits[0])))
            return False, retry_after
        hits.append(now)
    return True, 0


def validate_embed_request() -> tuple[Optional[EmbedClient], Optional[tuple]]:
    from flask import jsonify

    client = resolve_client_from_secret(get_client_key_from_request())
    if not client or not client.active:
        return None, (jsonify({"error": "Invalid or missing client key", "code": "INVALID_CLIENT_KEY"}), 401)

    origin = request.headers.get("Origin", "")
    if origin and not is_origin_allowed_for_client(origin, client):
        return None, (jsonify({"error": "Origin not allowed", "code": "ORIGIN_NOT_ALLOWED"}), 403)

    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    allowed, retry_after = check_rate_limit(f"{client.client_slug}:{ip}")
    if not allowed:
        resp = jsonify({"error": "Rate limit exceeded", "code": "RATE_LIMIT", "retry_after_seconds": retry_after})
        resp.status_code = 429
        resp.headers["Retry-After"] = str(retry_after)
        return None, (resp, 429)

    return client, None


def sync_embed_clients_from_env() -> None:
    from app.services.embed_service import create_embed_client, get_client_by_slug

    default_email = Config.EMBED_DEFAULT_OWNER_EMAIL or Config.MAIL_USERNAME or "admin@iqbalai.com"
    for slug, secret in _parse_env_client_keys().items():
        if get_client_by_slug(slug):
            continue
        try:
            create_embed_client(
                client_slug=slug,
                owner_email=default_email,
                secret=secret,
                allowed_origins=list(Config.ALLOWED_ORIGINS),
            )
            logger.info("Synced embed client from env: %s", slug)
        except Exception as exc:
            logger.warning("Could not sync embed client %s: %s", slug, exc)
