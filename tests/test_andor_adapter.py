from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from devices.andor_adapter import AndorCommunicationError, AndorKymeraSpectrometer
from devices.andor_sdk2 import AndorCameraSettings, AndorSDK2Error


@dataclass(frozen=True)
class FakeCameraCapabilities:
    model: str = "DU401A-BVF"
    serial_number: str = "CAM-1"

    def as_control_schema(self):
        return {"model": self.model, "serial_number": self.serial_number}


class FakeCamera:
    def __init__(self) -> None:
        self.capabilities = FakeCameraCapabilities()
        self.settings = AndorCameraSettings()
        self.max_intensity = 65535.0
        self.output_pixel_count = 4
        self.effective_pixel_width_um = 26.0
        self.applied: list[AndorCameraSettings] = []

    def apply_settings(self, settings) -> None:
        self.settings = settings
        self.applied.append(settings)
        self.output_pixel_count = 4 // settings.horizontal_binning
        self.effective_pixel_width_um = 26.0 * settings.horizontal_binning

    def acquire(self, exposure_ms: float, averages: int) -> np.ndarray:
        del exposure_ms, averages
        return np.arange(self.output_pixel_count, dtype=float)

    def get_temperature_c(self) -> float:
        return -70.0

    def health_check(self) -> str:
        return self.capabilities.serial_number

    def set_temperature_c(self, _value: float) -> None:
        pass

    def set_cooler_enabled(self, _enabled: bool) -> None:
        pass

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class FakeSpectrographCapabilities:
    serial_number: str = "KY-4444"

    def as_control_schema(self):
        return {"serial_number": self.serial_number, "gratings": []}


class FakeSpectrograph:
    def __init__(self) -> None:
        self.capabilities = FakeSpectrographCapabilities()
        self.pixel_count = 0
        self.pixel_width_um = 0.0
        self.center = 500.0
        self.grating = 1

    def configure_detector_geometry(self, *, pixel_count: int, pixel_width_um: float) -> None:
        self.pixel_count = pixel_count
        self.pixel_width_um = pixel_width_um

    def calibration_nm(self, pixel_count: int) -> np.ndarray:
        return np.linspace(400.0, 700.0, pixel_count)

    def state(self):
        return {"grating": self.grating, "center_wavelength_nm": self.center}

    def set_grating(self, value: int) -> None:
        self.grating = value

    def set_wavelength_nm(self, value: float) -> None:
        self.center = value

    def close(self) -> None:
        pass


def test_camera_geometry_configures_kymera_before_calibration() -> None:
    camera = FakeCamera()
    spectrograph = FakeSpectrograph()

    adapter = AndorKymeraSpectrometer(
        ".",
        camera=camera,
        spectrograph=spectrograph,
    )

    assert spectrograph.pixel_count == 4
    assert spectrograph.pixel_width_um == 26.0
    assert adapter.wavelengths_nm.shape == (4,)


def test_binning_updates_effective_geometry_and_calibration() -> None:
    camera = FakeCamera()
    spectrograph = FakeSpectrograph()
    adapter = AndorKymeraSpectrometer(".", camera=camera, spectrograph=spectrograph)

    adapter.apply_user_settings(
        {
            "camera": {"horizontal_binning": 2},
            "spectrograph": {"grating": 2, "center_wavelength_nm": 850.0},
        }
    )

    assert spectrograph.pixel_count == 2
    assert spectrograph.pixel_width_um == 52.0
    assert adapter.wavelengths_nm.shape == (2,)
    assert spectrograph.grating == 2
    assert spectrograph.center == 850.0


def test_acquisition_uses_calibrated_wavelength_grid() -> None:
    adapter = AndorKymeraSpectrometer(
        ".",
        camera=FakeCamera(),
        spectrograph=FakeSpectrograph(),
    )

    result = adapter.acquire_spectrum(
        integration_ms=10,
        averages=2,
        correct_dark=False,
        correct_nonlinearity=False,
    )

    np.testing.assert_allclose(result.wavelengths_nm, [400.0, 500.0, 600.0, 700.0])
    np.testing.assert_allclose(result.intensities_counts, [0.0, 1.0, 2.0, 3.0])


def test_unknown_spectrograph_setting_is_rejected_before_hardware_call() -> None:
    adapter = AndorKymeraSpectrometer(
        ".",
        camera=FakeCamera(),
        spectrograph=FakeSpectrograph(),
    )

    try:
        adapter.apply_user_settings(
            {"camera": {}, "spectrograph": {"mystery_setting": 1}}
        )
    except ValueError as exc:
        assert "Unknown Kymera setting" in str(exc)
    else:
        raise AssertionError("Unknown Andor settings must not be ignored.")


class FailingAcquisitionCamera(FakeCamera):
    def __init__(self, *, connected: bool) -> None:
        super().__init__()
        self.connected = bool(connected)

    def acquire(self, exposure_ms: float, averages: int) -> np.ndarray:
        del exposure_ms, averages
        raise AndorSDK2Error("simulated acquisition failure")

    def health_check(self) -> str:
        if not self.connected:
            raise AndorSDK2Error("simulated camera disconnect")
        return super().health_check()


def test_acquisition_rejection_does_not_claim_camera_disconnected() -> None:
    adapter = AndorKymeraSpectrometer(
        ".",
        camera=FailingAcquisitionCamera(connected=True),
        spectrograph=FakeSpectrograph(),
    )

    try:
        adapter.acquire_spectrum(
            integration_ms=10,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )
    except RuntimeError as exc:
        assert not isinstance(exc, AndorCommunicationError)
        assert "was rejected" in str(exc)
    else:
        raise AssertionError("A failed Andor acquisition must be reported.")


def test_acquisition_failure_with_failed_health_check_reports_disconnect() -> None:
    adapter = AndorKymeraSpectrometer(
        ".",
        camera=FailingAcquisitionCamera(connected=False),
        spectrograph=FakeSpectrograph(),
    )

    try:
        adapter.acquire_spectrum(
            integration_ms=10,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )
    except AndorCommunicationError as exc:
        assert "connection lost" in str(exc)
    else:
        raise AssertionError("A failed camera health check must report disconnect.")
