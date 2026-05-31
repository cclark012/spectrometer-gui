# core/preferences.py

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings


def get_bool(settings: QSettings, key: str, default: bool) -> bool:
    value = settings.value(key, default)

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}

    return bool(value)


def get_int(settings: QSettings, key: str, default: int) -> int:
    try:
        return int(settings.value(key, default))
    except Exception:
        return int(default)


def get_float(settings: QSettings, key: str, default: float) -> float:
    try:
        return float(settings.value(key, default))
    except Exception:
        return float(default)


def get_str(settings: QSettings, key: str, default: str) -> str:
    value = settings.value(key, default)

    if value is None:
        return str(default)

    return str(value)


def get_path(settings: QSettings, key: str, default: Path) -> Path:
    return Path(get_str(settings, key, str(default)))
