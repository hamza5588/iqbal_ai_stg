"""Platform-wide color theme (admin-managed branding for IqbalAI tenants)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from app.models.database_models import SystemSettings
from app.utils.db import get_db

logger = logging.getLogger(__name__)

THEME_SETTING_KEY = "platform_theme"

# Preset palettes — green matches current LMS default
THEME_PRESETS: Dict[str, Dict[str, str]] = {
    "green": {
        "primary": "#166534",
        "secondary": "#14532d",
        "light": "#dcfce7",
        "muted": "#f0fdf4",
        "name": "Iqbal Green",
    },
    "emerald": {
        "primary": "#10b981",
        "secondary": "#059669",
        "light": "#d1fae5",
        "muted": "#f0fdf4",
        "name": "Emerald",
    },
    "blue": {
        "primary": "#0ea5e9",
        "secondary": "#0284c7",
        "light": "#e0f2fe",
        "muted": "#f0f9ff",
        "name": "Sky Blue",
    },
    "indigo": {
        "primary": "#6366f1",
        "secondary": "#4f46e5",
        "light": "#e0e7ff",
        "muted": "#f0f4ff",
        "name": "Indigo",
    },
    "purple": {
        "primary": "#a855f7",
        "secondary": "#9333ea",
        "light": "#f3e8ff",
        "muted": "#faf5ff",
        "name": "Purple",
    },
    "rose": {
        "primary": "#f43f5e",
        "secondary": "#e11d48",
        "light": "#ffe4e6",
        "muted": "#fff1f2",
        "name": "Rose",
    },
    "orange": {
        "primary": "#f97316",
        "secondary": "#ea580c",
        "light": "#fed7aa",
        "muted": "#fff7ed",
        "name": "Orange",
    },
    "cyan": {
        "primary": "#06b6d4",
        "secondary": "#0891b2",
        "light": "#cffafe",
        "muted": "#ecf9ff",
        "name": "Cyan",
    },
    "teal": {
        "primary": "#14b8a6",
        "secondary": "#0d9488",
        "light": "#ccfbf1",
        "muted": "#f0fdfa",
        "name": "Teal",
    },
}

DEFAULT_PRESET = "green"


def _hex_to_rgb(hex_color: str) -> Optional[Tuple[int, int, int]]:
    m = re.match(r"^#?([a-fA-F0-9]{6})$", (hex_color or "").strip())
    if not m:
        return None
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _adjust_brightness(hex_color: str, percent: float) -> str:
    rgb = _hex_to_rgb(hex_color)
    if not rgb:
        return hex_color

    def adj(v: int) -> int:
        return max(0, min(255, int(v + v * percent / 100)))

    return f"#{adj(rgb[0]):02x}{adj(rgb[1]):02x}{adj(rgb[2]):02x}"


def _palette_from_primary(primary: str, name: str = "Custom") -> Dict[str, str]:
    return {
        "primary": primary,
        "secondary": _adjust_brightness(primary, -20),
        "light": _adjust_brightness(primary, 75),
        "muted": _adjust_brightness(primary, 90),
        "name": name,
    }


def _normalize_theme(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not data:
        preset = DEFAULT_PRESET
        palette = dict(THEME_PRESETS[preset])
        return {"preset": preset, **palette}

    preset = (data.get("preset") or DEFAULT_PRESET).strip().lower()
    if preset == "custom":
        primary = (data.get("primary") or THEME_PRESETS[DEFAULT_PRESET]["primary"]).strip()
        palette = _palette_from_primary(primary, data.get("name") or "Custom")
        return {"preset": "custom", **palette}

    if preset not in THEME_PRESETS:
        preset = DEFAULT_PRESET
    palette = dict(THEME_PRESETS[preset])
    return {"preset": preset, **palette}


def get_platform_theme(use_cache: bool = True) -> Dict[str, Any]:
    """Load theme from DB. Per-process cache is intentionally disabled so
    multi-worker deployments (gunicorn/uwsgi/docker replicas) never serve
    stale presets after an admin update on another worker."""
    del use_cache  # kept for API compatibility; always read fresh from DB

    db = get_db()
    row = db.query(SystemSettings).filter(SystemSettings.key == THEME_SETTING_KEY).first()
    parsed = None
    if row and row.value:
        try:
            parsed = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid platform_theme JSON in system_settings")

    return _normalize_theme(parsed if isinstance(parsed, dict) else None)


def list_theme_presets() -> Dict[str, Dict[str, str]]:
    return {k: dict(v) for k, v in THEME_PRESETS.items()}


def set_platform_theme(
    preset: Optional[str] = None,
    primary: Optional[str] = None,
    updated_by: Optional[int] = None,
) -> Dict[str, Any]:
    preset_key = (preset or DEFAULT_PRESET).strip().lower()
    primary_clean = (primary or "").strip()

    # Explicit primary hex always wins (supports preset + primary override in one request).
    if primary_clean and _hex_to_rgb(primary_clean):
        theme = _normalize_theme({"preset": "custom", "primary": primary_clean})
    elif preset_key == "custom":
        raise ValueError("Valid primary hex color is required for custom theme")
    elif preset_key in THEME_PRESETS:
        theme = _normalize_theme({"preset": preset_key})
    else:
        raise ValueError(f"Unknown theme preset: {preset_key}")

    db = get_db()
    payload = json.dumps(theme, ensure_ascii=False)
    row = db.query(SystemSettings).filter(SystemSettings.key == THEME_SETTING_KEY).first()
    if row:
        row.value = payload
        row.updated_by = updated_by
    else:
        db.add(
            SystemSettings(
                key=THEME_SETTING_KEY,
                value=payload,
                description="Platform-wide color theme for IqbalAI branding",
                updated_by=updated_by,
            )
        )
    db.commit()
    return get_platform_theme()


def theme_to_css_block(theme: Optional[Dict[str, Any]] = None) -> str:
    """Inline :root CSS for templates (avoids flash before JS loads)."""
    t = theme or get_platform_theme()
    primary = t["primary"]
    secondary = t.get("secondary") or _adjust_brightness(primary, -20)
    light = t.get("light") or _adjust_brightness(primary, 75)
    muted = t.get("muted") or _adjust_brightness(primary, 90)
    rgb = _hex_to_rgb(primary)
    rgb_str = f"{rgb[0]}, {rgb[1]}, {rgb[2]}" if rgb else "22, 101, 52"

    p50 = _adjust_brightness(primary, 95)
    p100 = light
    p300 = _adjust_brightness(primary, 40)
    p400 = _adjust_brightness(primary, 20)
    p500 = primary
    p600 = secondary
    p700 = _adjust_brightness(primary, -40)
    p800 = _adjust_brightness(primary, -55)
    p900 = _adjust_brightness(primary, -65)
    green_mid = _adjust_brightness(primary, 10)
    green_pale = _adjust_brightness(light, -8)
    border_soft = _adjust_brightness(light, -18)
    accent_light = _adjust_brightness(primary, 35)
    accent_bright = _adjust_brightness(primary, 55)

    return f""":root {{
  --primary-color: {primary};
  --primary-rgb: {rgb_str};
  --primary-50: {p50};
  --primary-100: {p100};
  --primary-300: {p300};
  --primary-400: {p400};
  --primary-500: {p500};
  --primary-600: {p600};
  --primary-700: {p700};
  --primary-800: {p800};
  --primary-900: {p900};
  --iqbal-primary: {primary};
  --iqbal-bg: {muted};
  --lms-green: {primary};
  --lms-green-dark: {secondary};
  --lms-green-light: {light};
  --lms-green-muted: {muted};
  --gradient-primary: linear-gradient(135deg, {primary} 0%, {secondary} 100%);
  --green: {primary};
  --green-dark: {secondary};
  --green-light: {light};
  --green-mid: {green_mid};
  --green-pale: {green_pale};
  --border-soft: {border_soft};
  --green-50: {p50};
  --green-100: {p100};
  --green-500: {p400};
  --green-600: {primary};
  --green-700: {p700};
  --green-900: {p900};
  --theme-shadow-focus: rgba({rgb_str}, 0.12);
  --theme-shadow-btn: rgba({rgb_str}, 0.35);
  --theme-shadow-soft: rgba({rgb_str}, 0.15);
  --theme-accent-light: {accent_light};
  --theme-accent-bright: {accent_bright};
  --vl-green-900: {p900};
  --vl-green-700: {p700};
  --vl-green-600: {primary};
  --vl-green-500: {p400};
  --brand-bar: {p900};
}}"""
