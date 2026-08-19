from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

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
    except (TypeError, ValueError):
        return int(default)


def get_float(settings: QSettings, key: str, default: float) -> float:
    try:
        return float(settings.value(key, default))
    except (TypeError, ValueError):
        return float(default)


def get_str(settings: QSettings, key: str, default: str) -> str:
    value = settings.value(key, default)
    return str(default) if value is None else str(value)


def _decode_value(raw: Any, current: Any) -> Any:
    if isinstance(current, bool):
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, Path):
        return Path(str(raw))
    if isinstance(current, tuple):
        if isinstance(raw, str):
            decoded = json.loads(raw)
        else:
            decoded = raw
        return tuple(decoded)
    if current is None:
        return raw
    return str(raw) if isinstance(current, str) else raw


def _encode_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return json.dumps(list(value))
    return value


def load_dataclass(settings: QSettings, prefix: str, target: object) -> None:
    """Load all existing fields of a mutable dataclass from QSettings."""

    if not is_dataclass(target):
        raise TypeError("target must be a dataclass instance")

    for field in fields(target):
        key = f"{prefix}/{field.name}"
        if not settings.contains(key):
            continue

        current = getattr(target, field.name)
        raw = settings.value(key)
        try:
            setattr(target, field.name, _decode_value(raw, current))
        except (TypeError, ValueError, json.JSONDecodeError):
            # Keep the current value if an old/corrupt preference cannot be parsed.
            continue


def save_dataclass(settings: QSettings, prefix: str, source: object) -> None:
    """Save all fields of a dataclass to QSettings."""

    if not is_dataclass(source):
        raise TypeError("source must be a dataclass instance")

    for field in fields(source):
        settings.setValue(
            f"{prefix}/{field.name}",
            _encode_value(getattr(source, field.name)),
        )
