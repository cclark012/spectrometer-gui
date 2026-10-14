from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import devices.andor_adapter as andor_adapter_module
from devices.andor_adapter import AndorCommunicationError, AndorKymeraSpectrometer
from devices.andor_sdk2 import AndorCameraSettings, AndorSDK2Error
from devices.andor_spectrograph import AndorSpectrographError


@dataclass(frozen=True)
class FakeCameraCapabilities:
    model: str = "DU401A-BVF"
    serial_number: str = "CAM-1"
    horizontal_speeds_mhz: dict[str, tuple[float, ...]] | None = None
    vertical_speeds_us: tuple[float, ...] = (8.25, 16.25)
    preamp_gains: tuple[float, ...] = (1.0,)

    def __post_init__(self) -> None:
        if self.horizontal_speeds_mhz is None:
            object.__setattr__(self, "horizontal_speeds_mhz", {"0:0": (0.1,)})

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
        self.last_target_midpoint_s = float("nan")
        self.stop_calls = 0

    def apply_settings(self, settings) -> None:
        self.settings = settings
        self.applied.append(settings)
        self.output_pixel_count = 4 // settings.horizontal_binning
        self.effective_pixel_width_um = 26.0 * settings.horizontal_binning

    def acquire(
        self,
        exposure_ms: float,
        averages: int,
        progress_callback=None,
        *,
        continuous_session: str = "",
        continuous_target_midpoint_s: float = float("nan"),
    ) -> np.ndarray:
        del exposure_ms, continuous_session
        self.last_target_midpoint_s = float(continuous_target_midpoint_s)
        if progress_callback is not None:
            progress_callback(0, averages, "software")
            progress_callback(averages, averages, "software")
        return np.arange(self.output_pixel_count, dtype=float)

    def get_temperature_c(self) -> float:
        return -70.0

    def health_check(self) -> str:
        return self.capabilities.serial_number

    def set_temperature_c(self, _value: float) -> None:
        pass

    def set_cooler_enabled(self, _enabled: bool) -> None:
        pass

    def stop_continuous_acquisition(self) -> None:
        self.stop_calls += 1

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
    assert camera.stop_calls == 1


def test_spectrograph_grating_readback_precedes_wavelength_command() -> None:
    class DelayedGratingSpectrograph(FakeSpectrograph):
        def __init__(self) -> None:
            super().__init__()
            self.pending_grating: int | None = None
            self.events: list[str] = []

        def state(self):
            if self.pending_grating is not None:
                self.events.append("grating_readback")
                self.grating = self.pending_grating
                self.pending_grating = None
            return super().state()

        def set_grating(self, value: int) -> None:
            self.events.append("set_grating")
            self.pending_grating = int(value)

        def set_wavelength_nm(self, value: float) -> None:
            self.events.append("set_wavelength")
            super().set_wavelength_nm(value)

    camera = FakeCamera()
    spectrograph = DelayedGratingSpectrograph()
    adapter = AndorKymeraSpectrometer(
        ".",
        camera=camera,
        spectrograph=spectrograph,
    )

    adapter.apply_user_settings(
        {
            "camera": {},
            "spectrograph": {"grating": 2, "center_wavelength_nm": 850.0},
        }
    )

    assert spectrograph.events == [
        "set_grating",
        "grating_readback",
        "set_wavelength",
    ]


def test_spectrograph_readback_mismatch_is_an_explicit_failure() -> None:
    adapter = AndorKymeraSpectrometer(
        ".",
        camera=FakeCamera(),
        spectrograph=FakeSpectrograph(),
    )

    try:
        adapter._wait_for_spectrograph_readback(
            {"grating": 2},
            timeout_s=0.0,
        )
    except AndorSpectrographError as exc:
        assert "requested 2" in str(exc)
        assert "read back 1" in str(exc)
    else:
        raise AssertionError("A mismatched Kymera readback was accepted.")


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


def test_acquisition_reports_average_progress() -> None:
    adapter = AndorKymeraSpectrometer(
        ".",
        camera=FakeCamera(),
        spectrograph=FakeSpectrograph(),
    )
    progress = []

    adapter.acquire_spectrum(
        integration_ms=10,
        averages=3,
        correct_dark=False,
        correct_nonlinearity=False,
        progress_callback=lambda *values: progress.append(values),
    )

    assert progress == [(0, 3, "software"), (3, 3, "software")]


def test_gated_midpoint_target_is_forwarded_to_sdk2_camera() -> None:
    camera = FakeCamera()
    adapter = AndorKymeraSpectrometer(
        ".",
        camera=camera,
        spectrograph=FakeSpectrograph(),
    )

    adapter.acquire_spectrum(
        integration_ms=10,
        averages=1,
        correct_dark=False,
        correct_nonlinearity=False,
        continuous_session="gated",
        continuous_target_midpoint_s=123.456,
    )

    assert np.isclose(camera.last_target_midpoint_s, 123.456)


def test_metadata_snapshot_uses_compact_readback_state() -> None:
    adapter = AndorKymeraSpectrometer(
        ".",
        camera=FakeCamera(),
        spectrograph=FakeSpectrograph(),
    )

    state = adapter.metadata_snapshot()

    assert state["grating"] == 1
    assert state["center_wavelength_nm"] == 500.0
    assert state["horizontal_readout_mhz"] == 0.1
    assert state["vertical_shift_us"] == 8.25
    assert state["preamp_gain"] == 1.0
    assert state["horizontal_binning"] == 1


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

    def acquire(
        self,
        exposure_ms: float,
        averages: int,
        progress_callback=None,
        *,
        continuous_session: str = "",
        continuous_target_midpoint_s: float = float("nan"),
    ) -> np.ndarray:
        del (
            exposure_ms,
            averages,
            progress_callback,
            continuous_session,
            continuous_target_midpoint_s,
        )
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


def test_real_constructor_reports_kymera_success_before_camera_failure() -> None:
    events: list[str] = []

    class ConnectedKymera(FakeSpectrograph):
        def __init__(self, *_args, **_kwargs) -> None:
            events.append("kymera")
            super().__init__()

    class MissingCamera:
        def __init__(self, *_args, **_kwargs) -> None:
            events.append("camera")
            raise AndorSDK2Error("camera unavailable")

    original_kymera = andor_adapter_module.AndorKymera
    original_camera = andor_adapter_module.AndorSDK2Camera
    andor_adapter_module.AndorKymera = ConnectedKymera
    andor_adapter_module.AndorSDK2Camera = MissingCamera
    try:
        try:
            AndorKymeraSpectrometer(".")
        except AndorSDK2Error as exc:
            assert "Kymera KY-4444 connected successfully" in str(exc)
            assert "camera unavailable" in str(exc)
        else:
            raise AssertionError("A missing iDus camera must reject the combined backend.")
    finally:
        andor_adapter_module.AndorKymera = original_kymera
        andor_adapter_module.AndorSDK2Camera = original_camera

    assert events == ["kymera", "camera"]
