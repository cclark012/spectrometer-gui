from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from devices.qepro_adapter import (
    QEProSpectrometer,
    SpectrometerCommandError,
    SpectrometerCommunicationError,
)


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
        self.integration_set_count = 0
        self.intensity_read_count = 0
        self.frames: list[np.ndarray] = []

    def wavelengths(self) -> np.ndarray:
        return np.array([400.0, 500.0, 600.0])

    def set_scans_to_average(self, averages: int) -> None:
        self.last_averages = int(averages)

    def integration_time_micros(self, value: int) -> None:
        self.last_integration_us = int(value)
        self.integration_set_count += 1

    def intensities(self, **_kwargs) -> np.ndarray:
        self.intensity_read_count += 1
        if self.frames:
            return self.frames.pop(0).copy()
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
    adapter._applied_integration_us = None
    adapter._applied_device_averages = None
    return adapter


def test_constructor_opens_configured_serial_number() -> None:
    calls: list[tuple[str, str]] = []

    class FakeFactory:
        @classmethod
        def from_serial_number(cls, serial: str):
            calls.append(("serial", serial))
            return FakeSpectrometer()

        @classmethod
        def from_first_available(cls):
            calls.append(("first", ""))
            return FakeSpectrometer()

    package = types.ModuleType("seabreeze")
    module = types.ModuleType("seabreeze.spectrometers")
    module.Spectrometer = FakeFactory
    original_package = sys.modules.get("seabreeze")
    original_module = sys.modules.get("seabreeze.spectrometers")
    sys.modules["seabreeze"] = package
    sys.modules["seabreeze.spectrometers"] = module
    try:
        adapter = QEProSpectrometer(serial_number=" QEP05831 ")
    finally:
        if original_package is None:
            sys.modules.pop("seabreeze", None)
        else:
            sys.modules["seabreeze"] = original_package
        if original_module is None:
            sys.modules.pop("seabreeze.spectrometers", None)
        else:
            sys.modules["seabreeze.spectrometers"] = original_module

    assert adapter.serial_number == "FAKE"
    assert calls == [("serial", "QEP05831")]


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


def test_first_frame_after_integration_change_is_discarded() -> None:
    spec = FakeSpectrometer()
    spec.frames = [
        np.array([100.0, 100.0, 100.0]),
        np.array([10.0, 20.0, 30.0]),
    ]
    adapter = make_adapter(spec)
    adapter._applied_integration_us = 1_000_000
    adapter._applied_device_averages = 1

    result = adapter.acquire_spectrum(
        integration_ms=10,
        averages=1,
        correct_dark=False,
        correct_nonlinearity=False,
    )

    np.testing.assert_array_equal(
        result.intensities_counts,
        np.array([10.0, 20.0, 30.0]),
    )
    assert spec.intensity_read_count == 2
    assert spec.integration_set_count == 1


def test_unchanged_integration_does_not_discard_or_reconfigure() -> None:
    spec = FakeSpectrometer()
    spec.frames = [np.array([10.0, 20.0, 30.0])]
    adapter = make_adapter(spec)
    adapter._applied_integration_us = 10_000
    adapter._applied_device_averages = 1

    result = adapter.acquire_spectrum(
        integration_ms=10,
        averages=1,
        correct_dark=False,
        correct_nonlinearity=False,
    )

    np.testing.assert_array_equal(
        result.intensities_counts,
        np.array([10.0, 20.0, 30.0]),
    )
    assert spec.intensity_read_count == 1
    assert spec.integration_set_count == 0


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


class FakeThermo:
    def __init__(self) -> None:
        self.target_c: float | None = None
        self.temperature_c = 20.0
        self.set_error: Exception | None = None
        self.read_error: Exception | None = None

    def set_temperature_setpoint_degrees_celsius(self, value: float) -> None:
        if self.set_error is not None:
            raise self.set_error
        self.target_c = float(value)

    def read_temperature_degrees_celsius(self) -> float:
        if self.read_error is not None:
            raise self.read_error
        return float(self.temperature_c)


def attach_thermo(adapter: QEProSpectrometer, thermo: FakeThermo) -> None:
    adapter.spec.features["thermo_electric"] = [thermo]


@pytest.mark.parametrize("target_c", [float("nan"), float("inf"), -40.0, 50.0])
def test_invalid_tec_target_is_rejected_before_usb(target_c: float) -> None:
    adapter = make_adapter()
    thermo = FakeThermo()
    attach_thermo(adapter, thermo)

    with pytest.raises(ValueError, match="TEC target"):
        adapter.set_tec_target_c(target_c)

    assert thermo.target_c is None


def test_tec_transfer_error_with_successful_health_check_stays_connected() -> None:
    class SeaBreezeError(RuntimeError):
        pass

    adapter = make_adapter()
    thermo = FakeThermo()
    thermo.set_error = SeaBreezeError("Data transfer error")
    thermo.temperature_c = 22.5
    attach_thermo(adapter, thermo)

    with pytest.raises(SpectrometerCommandError, match="remains connected"):
        adapter.set_tec_target_c(10.0)


def test_tec_transfer_error_with_failed_health_check_disconnects() -> None:
    class SeaBreezeError(RuntimeError):
        pass

    adapter = make_adapter()
    thermo = FakeThermo()
    thermo.set_error = SeaBreezeError("Data transfer error")
    thermo.read_error = SeaBreezeError("Device not found")
    attach_thermo(adapter, thermo)

    with pytest.raises(
        SpectrometerCommunicationError,
        match="Follow-up CCD temperature readout also failed",
    ):
        adapter.set_tec_target_c(10.0)


def test_seabreeze_transport_failure_is_normalized() -> None:
    class SeaBreezeError(RuntimeError):
        pass

    class DisconnectedSpectrometer(FakeSpectrometer):
        def intensities(self, **_kwargs) -> np.ndarray:
            raise SeaBreezeError("USB data transfer error")

    adapter = make_adapter(DisconnectedSpectrometer())
    with pytest.raises(SpectrometerCommunicationError, match="spectrum readout"):
        adapter.acquire_spectrum(
            integration_ms=10,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )


def test_non_transport_backend_error_is_not_reclassified() -> None:
    class InvalidDataSpectrometer(FakeSpectrometer):
        def intensities(self, **_kwargs) -> np.ndarray:
            raise ValueError("bad correction setting")

    adapter = make_adapter(InvalidDataSpectrometer())
    with pytest.raises(ValueError, match="bad correction setting"):
        adapter.acquire_spectrum(
            integration_ms=10,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )


def test_usb_capability_error_is_not_mistaken_for_disconnect() -> None:
    class UnsupportedFeatureSpectrometer(FakeSpectrometer):
        def intensities(self, **_kwargs) -> np.ndarray:
            raise ValueError("USB feature is not supported by this backend")

    adapter = make_adapter(UnsupportedFeatureSpectrometer())
    with pytest.raises(ValueError, match="USB feature is not supported"):
        adapter.acquire_spectrum(
            integration_ms=10,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )
