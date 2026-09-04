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
    assert config.spectrometer_backend == "auto"


def test_andor_backend_options_are_normalized() -> None:
    config = build_device_config(
        namespace(real=True),
        {
            "spectrometer_backend": "ANDOR",
            "andor_solis_dir": "C:/Andor",
            "andor_camera_index": 1,
            "andor_spectrograph_index": 2,
        },
    )
    assert config.spectrometer_backend == "andor"
    assert config.andor_solis_dir == Path("C:/Andor")
    assert config.andor_camera_index == 1
    assert config.andor_spectrograph_index == 2
    assert config.andor_camera_dll == Path("atmcd64d_legacy.dll")


def test_auto_spectrometer_backend_is_accepted() -> None:
    config = build_device_config(
        namespace(real=True, spectrometer_backend="auto"),
        {},
    )
    assert config.spectrometer_backend == "auto"
    assert config.spectrometer_mode == "real"


def test_instrument_modes_are_independent() -> None:
    config = build_device_config(
        namespace(),
        {
            "spectrometer_mode": "real",
            "spectrometer_backend": "andor",
            "qepro_serial_number": "QEP05831",
            "power_meter_mode": "emulated",
            "laser_mode": "disconnected",
            "spectrometer_fallback_emulator": False,
            "power_meter_fallback_emulator": True,
        },
    )

    assert config.spectrometer_mode == "real"
    assert config.spectrometer_backend == "andor"
    assert config.qepro_serial_number == "QEP05831"
    assert config.power_meter_mode == "emulated"
    assert config.laser_mode == "disconnected"
    assert not config.spectrometer_fallback_emulator
    assert config.power_meter_fallback_emulator
    assert not config.emulate


def test_runtime_selection_does_not_change_other_instrument_modes() -> None:
    config = build_device_config(namespace(), {})
    original_power_mode = config.power_meter_mode
    original_laser_mode = config.laser_mode

    config.select_spectrometer("real", "andor")

    assert config.spectrometer_mode == "real"
    assert config.spectrometer_backend == "andor"
    assert config.power_meter_mode == original_power_mode
    assert config.laser_mode == original_laser_mode


def test_per_instrument_cli_mode_overrides_shared_real_preset() -> None:
    config = build_device_config(
        namespace(
            real=True,
            spectrometer_mode="emulated",
            power_meter_mode="disconnected",
        ),
        {},
    )

    assert config.spectrometer_mode == "emulated"
    assert config.power_meter_mode == "disconnected"


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
