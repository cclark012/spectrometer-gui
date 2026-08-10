from __future__ import annotations

import numpy as np
import pytest

from devices.qepro_adapter import QEProSpectrometer


class FakeFeature:
    def ping(self) -> None:
        pass


class FakeSpectrometer:
    def __init__(self) -> None:
        self.features = {
            "thermo_electric": [],
            "available_feature": [FakeFeature()],
        }
        self.integration_time_micros_limits = (8_000, 60_000_000)
        self.model = "QEPro"
        self.serial_number = "FAKE"
        self.pixels = 3
        self.max_intensity = 65_535.0
        self.last_averages = 1
        self.last_integration_us = 0
        self.values = np.array([1.0, 2.0, 3.0])

    def set_scans_to_average(self, averages: int) -> None:
        self.last_averages = int(averages)

    def integration_time_micros(self, value: int) -> None:
        self.last_integration_us = int(value)

    def intensities(self, **_kwargs) -> np.ndarray:
        return self.values.copy()


def make_adapter(spec: FakeSpectrometer | None = None) -> QEProSpectrometer:
    adapter = QEProSpectrometer.__new__(QEProSpectrometer)
    adapter.spec = spec or FakeSpectrometer()
    adapter.wavelengths_nm = np.array([400.0, 500.0, 600.0])
    adapter.name = "QEPro"
    adapter.serial_number = "FAKE"
    adapter.max_intensity = 65_535.0
    adapter._capabilities_cache = None
    adapter._hardware_average_method_checked = False
    adapter._hardware_average_method = None
    return adapter


def test_capabilities_exclude_unavailable_feature_lists() -> None:
    adapter = make_adapter()
    capabilities = adapter.capabilities()
    assert capabilities.features == ["available_feature"]
    assert not capabilities.tec_supported
    assert capabilities.device_averaging_supported


def test_top_level_device_averaging_method_is_used() -> None:
    adapter = make_adapter()
    result = adapter.acquire_spectrum(
        integration_ms=10,
        averages=5,
        correct_dark=False,
        correct_nonlinearity=False,
        averaging_mode="device",
    )
    assert result.device_averaging_used
    assert adapter.spec.last_averages == 5
    assert adapter.spec.last_integration_us == 10_000


def test_unknown_averaging_mode_fails() -> None:
    adapter = make_adapter()
    with pytest.raises(ValueError, match="Unknown averaging mode"):
        adapter.acquire_spectrum(
            integration_ms=10,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
            averaging_mode="invalid",
        )


def test_intensity_shape_must_match_wavelength_shape() -> None:
    spec = FakeSpectrometer()
    spec.values = np.array([1.0, 2.0])
    adapter = make_adapter(spec)
    with pytest.raises(RuntimeError, match="different shapes"):
        adapter.acquire_spectrum(
            integration_ms=10,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )


def test_integration_limit_fallback_is_qepro_safe() -> None:
    adapter = make_adapter()
    adapter.spec.integration_time_micros_limits = None
    assert adapter._integration_limits_us() == (8_000, 60_000_000)
