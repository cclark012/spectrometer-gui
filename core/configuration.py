from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from core.settings import DeviceConfig


class ConfigurationError(ValueError):
    """Raised when the lab-default configuration cannot be interpreted."""


def load_json_defaults(path: str | Path | None) -> dict[str, Any]:
    """Load an optional JSON object containing lab-specific defaults."""

    if not path:
        return {}

    config_path = Path(path)
    if not config_path.exists():
        return {}

    try:
        with config_path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"Could not read configuration file {config_path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise ConfigurationError(
            f"Configuration file {config_path} must contain a JSON object."
        )

    return value


def _value(args: Namespace, defaults: dict[str, Any], key: str, fallback: Any) -> Any:
    command_line_value = getattr(args, key, None)
    if command_line_value is not None:
        return command_line_value
    return defaults.get(key, fallback)


def _boolean(value: Any, *, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ConfigurationError(f"{key!r} must be a boolean value.")


def _obis_ports(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        ports = [value]
    elif isinstance(value, (list, tuple)):
        ports = [str(item).strip() for item in value]
    else:
        raise ConfigurationError("'obis_ports' must be a string, list, or null.")

    ports = [port for port in ports if port]
    return ports or None


def build_device_config(args: Namespace, defaults: dict[str, Any]) -> DeviceConfig:
    """Resolve CLI options and JSON defaults into a validated DeviceConfig."""

    power_channel_raw = _value(args, defaults, "power_channel", 1)
    try:
        power_channel = int(power_channel_raw)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("'power_channel' must be an integer.") from exc
    if power_channel < 1:
        raise ConfigurationError("'power_channel' must be at least 1.")

    laser_mode = str(_value(args, defaults, "laser_mode", "auto")).strip().lower()
    if laser_mode not in {"real", "emulated", "auto"}:
        raise ConfigurationError(
            "'laser_mode' must be one of: real, emulated, auto."
        )

    fallback_emulator = _boolean(
        _value(args, defaults, "fallback_emulator", False),
        key="fallback_emulator",
    )
    newport_dll_value = _value(
        args,
        defaults,
        "newport_dll",
        r"C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll",
    )
    newport_dll = Path(str(newport_dll_value)) if newport_dll_value else None

    emulate_main_devices = not bool(getattr(args, "real", False))
    if bool(getattr(args, "emulate", False)):
        emulate_main_devices = True

    return DeviceConfig(
        emulate=emulate_main_devices,
        fallback_emulator=fallback_emulator,
        newport_dll=newport_dll,
        power_channel=power_channel,
        emulate_lasers=(laser_mode == "emulated"),
        laser_fallback_emulator=(laser_mode == "auto"),
        obis_ports=_obis_ports(_value(args, defaults, "obis_ports", None)),
    )
