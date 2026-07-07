"""Helpers for validating website URLs and updating the global ALLOWED_ORIGINS whitelist."""

from __future__ import annotations

import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

from app.config import Config

logger = logging.getLogger(__name__)

_ORIGIN_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_env_file_path() -> str:
    return os.path.join(_project_root(), ".env")


def validate_owner_email(email: str) -> str:
    """Reuse the same email pattern as embed_service contact extraction."""
    from app.services.embed_service import EMAIL_RE

    cleaned = (email or "").strip().lower()
    if not cleaned or not EMAIL_RE.fullmatch(cleaned):
        raise ValueError("Invalid owner email address")
    return cleaned


def validate_and_normalize_website_url(url: str) -> str:
    """
    Normalize a website URL to scheme + host (no path/query).
    Accepts input with or without scheme; defaults to https.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Website URL is required")

    if not _ORIGIN_SCHEME_RE.match(raw):
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.netloc or "").lower().rstrip(".")

    if scheme not in ("http", "https"):
        raise ValueError("Website URL must use http or https")
    if not host or "." not in host:
        raise ValueError("Invalid website URL host")
    if parsed.username or parsed.password:
        raise ValueError("Website URL must not include credentials")

    return f"{scheme}://{host}"


def origins_from_website_url(url: str) -> list[str]:
    """Return canonical origin plus www/non-www variant for embed CORS checks."""
    normalized = validate_and_normalize_website_url(url)
    parsed = urlparse(normalized)
    scheme = parsed.scheme
    host = parsed.netloc

    origins = [f"{scheme}://{host}"]
    if host.startswith("www."):
        bare = host[4:]
        if bare:
            origins.append(f"{scheme}://{bare}")
    else:
        origins.append(f"{scheme}://www.{host}")

    seen: set[str] = set()
    unique: list[str] = []
    for origin in origins:
        if origin not in seen:
            seen.add(origin)
            unique.append(origin)
    return unique


def generate_client_slug_from_url(url: str) -> str:
    """Derive a short client slug from the website hostname (e.g. lmda.com.pk -> lmda)."""
    normalized = validate_and_normalize_website_url(url)
    host = urlparse(normalized).netloc
    if host.startswith("www."):
        host = host[4:]

    label = host.split(".")[0]
    slug = _SLUG_RE.sub("", label.lower())
    if not slug:
        raise ValueError("Could not derive client slug from website URL")
    return slug[:64]


def _parse_env_allowed_origins(raw: str) -> list[str]:
    return [o.strip() for o in (raw or "").split(",") if o.strip()]


def _serialize_env_allowed_origins(origins: list[str]) -> str:
    return ",".join(origins)


def add_origins_to_global_whitelist(origins: list[str]) -> list[str]:
    """
    Append origins to ALLOWED_ORIGINS in .env (if present) and update runtime Config.
    Preserves existing entries and skips duplicates. Returns newly added origins.
    """
    to_add = [o.strip() for o in origins if o and o.strip()]
    if not to_add:
        return []

    current = list(Config.ALLOWED_ORIGINS or [])
    existing = set(current)
    newly_added = [o for o in to_add if o not in existing]
    if not newly_added:
        return []

    updated = current + newly_added
    Config.ALLOWED_ORIGINS = updated

    env_path = get_env_file_path()
    if os.path.isfile(env_path):
        try:
            _update_env_allowed_origins_file(env_path, updated)
        except Exception as exc:
            logger.error("Failed to update ALLOWED_ORIGINS in .env: %s", exc, exc_info=True)
            raise RuntimeError("Failed to update global whitelist configuration") from exc
    else:
        logger.warning("No .env file at %s; updated runtime ALLOWED_ORIGINS only", env_path)

    return newly_added


def _update_env_allowed_origins_file(env_path: str, all_origins: list[str]) -> None:
    serialized = _serialize_env_allowed_origins(all_origins)
    with open(env_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    key = "ALLOWED_ORIGINS"
    found = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={serialized}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] = new_lines[-1] + "\n"
        new_lines.append(f"{key}={serialized}\n")

    with open(env_path, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)
