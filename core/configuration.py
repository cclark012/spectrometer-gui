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


def _instrument_mode(value: Any, *, key: str, allow_auto: bool = False) -> str:
    mode = str(value).strip().lower()
    allowed = {"real", "emulated", "disconnected"}
    if allow_auto:
        allowed.add("auto")
    if mode not in allowed:
        raise ConfigurationError(
            f"{key!r} must be one of: {', '.join(sorted(allowed))}."
        )
    return mode


def build_device_config(args: Namespace, defaults: dict[str, Any]) -> DeviceConfig:
    """Resolve CLI options and JSON defaults into a validated DeviceConfig."""

    power_channel_raw = _value(args, defaults, "power_channel", 1)
    try:
        power_channel = int(power_channel_raw)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("'power_channel' must be an integer.") from exc
    if power_channel < 1:
        raise ConfigurationError("'power_channel' must be at least 1.")

    spectrometer_backend = str(
        _value(args, defaults, "spectrometer_backend", "qepro")
    ).strip().lower()
    if spectrometer_backend not in {"qepro", "andor"}:
        raise ConfigurationError(
            "'spectrometer_backend' must be one of: qepro, andor."
        )

    def nonnegative_index(key: str) -> int:
        try:
            value = int(_value(args, defaults, key, 0))
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"{key!r} must be an integer.") from exc
        if value < 0:
            raise ConfigurationError(f"{key!r} must not be negative.")
        return value

    preset_mode: str | None = None
    if bool(getattr(args, "real", False)):
        preset_mode = "real"
    elif bool(getattr(args, "emulate", False)):
        preset_mode = "emulated"

    def requested_mode(key: str, default: str) -> Any:
        command_line_mode = getattr(args, key, None)
        if command_line_mode is not None:
            return command_line_mode
        if preset_mode is not None:
            return preset_mode
        return defaults.get(key, default)

    spectrometer_mode = _instrument_mode(
        requested_mode("spectrometer_mode", "emulated"),
        key="spectrometer_mode",
    )
    power_meter_mode = _instrument_mode(
        requested_mode("power_meter_mode", "emulated"),
        key="power_meter_mode",
    )
    laser_mode = _instrument_mode(
        _value(args, defaults, "laser_mode", "auto"),
        key="laser_mode",
        allow_auto=True,
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
    andor_solis_value = _value(
        args,
        defaults,
        "andor_solis_dir",
        r"C:\Program Files\Andor SOLIS",
    )
    andor_solis_dir = Path(str(andor_solis_value)) if andor_solis_value else None

    spectrometer_fallback = _boolean(
        _value(
            args,
            defaults,
            "spectrometer_fallback_emulator",
            fallback_emulator,
        ),
        key="spectrometer_fallback_emulator",
    )
    power_meter_fallback = _boolean(
        _value(
            args,
            defaults,
            "power_meter_fallback_emulator",
            fallback_emulator,
        ),
        key="power_meter_fallback_emulator",
    )

    return DeviceConfig(
        emulate=(
            spectrometer_mode == "emulated"
            and power_meter_mode == "emulated"
        ),
        fallback_emulator=fallback_emulator,
        newport_dll=newport_dll,
        power_channel=power_channel,
        spectrometer_backend=spectrometer_backend,
        qepro_serial_number=str(
            _value(args, defaults, "qepro_serial_number", "") or ""
        ).strip(),
        andor_solis_dir=andor_solis_dir,
        andor_camera_index=nonnegative_index("andor_camera_index"),
        andor_spectrograph_index=nonnegative_index("andor_spectrograph_index"),
        emulate_lasers=(laser_mode == "emulated"),
        laser_fallback_emulator=(laser_mode == "auto"),
        obis_ports=_obis_ports(_value(args, defaults, "obis_ports", None)),
        spectrometer_mode=spectrometer_mode,
        power_meter_mode=power_meter_mode,
        laser_mode=laser_mode,
        spectrometer_fallback_emulator=spectrometer_fallback,
        power_meter_fallback_emulator=power_meter_fallback,
    )
