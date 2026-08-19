from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from core.configuration import ConfigurationError, build_device_config, load_json_defaults


def namespace(**values):
    defaults = {
        "real": False,
        "emulate": False,
        "newport_dll": None,
        "power_channel": None,
        "laser_mode": None,
        "obis_ports": None,
        "fallback_emulator": None,
    }
    defaults.update(values)
    return argparse.Namespace(**defaults)


def test_build_device_config_validates_and_normalizes_values() -> None:
    config = build_device_config(
        namespace(real=True),
        {
            "power_channel": 2,
            "laser_mode": "AUTO",
            "obis_ports": "COM8",
            "fallback_emulator": False,
        },
    )
    assert not config.emulate
    assert config.power_channel == 2
    assert config.laser_fallback_emulator
    assert config.obis_ports == ["COM8"]


def test_invalid_laser_mode_fails() -> None:
    with pytest.raises(ConfigurationError, match="laser_mode"):
        build_device_config(namespace(), {"laser_mode": "invalid"})


def test_invalid_boolean_fails() -> None:
    with pytest.raises(ConfigurationError, match="fallback_emulator"):
        build_device_config(namespace(), {"fallback_emulator": "sometimes"})


def test_load_json_defaults_requires_object(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="JSON object"):
        load_json_defaults(path)
