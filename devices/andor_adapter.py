from __future__ import annotations

from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Any

import numpy as np

from core.records import SpectralAcquisition, SpectrometerCapabilities
from devices.andor_sdk2 import (
    AndorCameraSettings,
    AndorSDK2Camera,
    AndorSDK2Error,
)
from devices.andor_spectrograph import AndorKymera, AndorSpectrographError
from devices.errors import SpectrometerCommunicationError


class AndorCommunicationError(SpectrometerCommunicationError):
    pass


class AndorKymeraSpectrometer:
    """Combined iDus camera + Kymera adapter implementing SpectrometerAdapter."""

    def __init__(
        self,
        solis_dir: str | Path,
        *,
        camera_index: int = 0,
        spectrograph_index: int = 0,
        camera=None,
        spectrograph=None,
    ) -> None:
        self.solis_dir = Path(solis_dir)
        self.camera = camera
        self.spectrograph = spectrograph
        try:
            if self.camera is None:
                self.camera = AndorSDK2Camera(
                    self.solis_dir,
                    camera_index=int(camera_index),
                )
            if self.spectrograph is None:
                self.spectrograph = AndorKymera(
                    self.solis_dir,
                    device_index=int(spectrograph_index),
                )
            self.name = (
                f"Andor {self.camera.capabilities.model} + Kymera "
                f"{self.spectrograph.capabilities.serial_number}"
            )
            self.serial_number = str(self.camera.capabilities.serial_number)
            self.max_intensity = float(self.camera.max_intensity)
            self._configure_calibration_geometry()
        except Exception:
            self.close()
            raise

    def _configure_calibration_geometry(self) -> None:
        pixel_count = int(self.camera.output_pixel_count)
        pixel_width_um = float(self.camera.effective_pixel_width_um)
        if pixel_count <= 0 or pixel_width_um <= 0:
            raise ValueError(
                "The selected iDus did not provide usable pixel geometry; "
                "Kymera calibration cannot be configured."
            )
        self.spectrograph.configure_detector_geometry(
            pixel_count=pixel_count,
            pixel_width_um=pixel_width_um,
        )
        self.wavelengths_nm = self.spectrograph.calibration_nm(pixel_count)

    def capabilities(self) -> SpectrometerCapabilities:
        camera_schema = self.camera.capabilities.as_control_schema()
        spectrograph_schema = self.spectrograph.capabilities.as_control_schema()
        return SpectrometerCapabilities(
            model=self.name,
            serial_number=self.serial_number,
            pixels=int(self.camera.output_pixel_count),
            max_intensity=self.max_intensity,
            integration_time_min_us=1_000,
            integration_time_max_us=3_600_000_000,
            features=[
                "andor_sdk2_camera",
                "kymera_spectrograph",
                "full_vertical_binning",
                "software_averaging",
            ],
            feature_methods={
                "andor_camera": [
                    "AD channel",
                    "output amplifier",
                    "horizontal/vertical readout speed",
                    "preamp gain",
                    "horizontal binning",
                    "cooler/temperature",
                ],
                "kymera": [
                    "center wavelength",
                    "grating",
                    "filter wheel when installed",
                    "flipper/output port",
                    "focus mirror",
                    "grating and detector offsets",
                ],
            },
            tec_supported=True,
            device_averaging_supported=False,
            electric_dark_correction_supported=False,
            nonlinearity_correction_supported=False,
            backend="andor",
            control_schema={
                "camera": camera_schema,
                "spectrograph": spectrograph_schema,
                "state": self.user_state(),
                "step_and_glue": {
                    "supported": False,
                    "reason": (
                        "Step-and-Glue is a software scan/merge workflow and will be "
                        "enabled after single-frame hardware calibration is verified."
                    ),
                },
            },
        )

    def user_state(self) -> dict[str, Any]:
        return {
            "camera": asdict(self.camera.settings),
            "spectrograph": self.spectrograph.state(),
        }

    def _normalize_operation_error(
        self,
        operation: str,
        exc: Exception,
    ) -> Exception:
        """Use one on-demand health check to distinguish rejection from loss."""

        health_errors: list[str] = []
        try:
            health_check = getattr(self.camera, "health_check", None)
            if callable(health_check):
                health_check()
            else:
                self.camera.get_temperature_c()
        except Exception as health_exc:
            health_errors.append(f"camera: {health_exc}")
        try:
            self.spectrograph.state()
        except Exception as health_exc:
            health_errors.append(f"spectrograph: {health_exc}")
        if health_errors:
            return AndorCommunicationError(
                f"Andor connection lost during {operation}: {exc}. "
                "Follow-up health check failed (" + "; ".join(health_errors) + ")."
            )
        return RuntimeError(f"Andor {operation} was rejected: {exc}")

    def _validate_spectrograph_settings(self, values: dict[str, Any]) -> None:
        """Reject an invalid multi-control update before changing any hardware."""

        capabilities = self.spectrograph.capabilities
        gratings = tuple(getattr(capabilities, "gratings", ()))
        current_grating = int(self.spectrograph.state()["grating"])
        target_grating = int(values.get("grating", current_grating))
        valid_gratings = {int(item.index) for item in gratings}
        if valid_gratings and target_grating not in valid_gratings:
            raise ValueError(
                f"Unknown Kymera grating {target_grating}; "
                f"valid values are {sorted(valid_gratings)}."
            )

        if "center_wavelength_nm" in values:
            center_nm = float(values["center_wavelength_nm"])
            target = next(
                (item for item in gratings if int(item.index) == target_grating),
                None,
            )
            if target is not None and not (
                float(target.minimum_nm) <= center_nm <= float(target.maximum_nm)
            ):
                raise ValueError(
                    f"Kymera wavelength {center_nm:g} nm is outside grating "
                    f"{target_grating}'s range [{float(target.minimum_nm):g}, "
                    f"{float(target.maximum_nm):g}] nm."
                )

        if "filter_position" in values:
            valid_filters = tuple(getattr(capabilities, "filter_positions", ()))
            if int(values["filter_position"]) not in valid_filters:
                raise ValueError("The requested Kymera filter position is unavailable.")

        if "focus_mirror_position" in values:
            if not bool(getattr(capabilities, "focus_mirror_present", False)):
                raise ValueError("This Kymera does not report a focus mirror.")
            focus_position = int(values["focus_mirror_position"])
            focus_maximum = int(
                getattr(capabilities, "focus_mirror_max_steps", 0)
            )
            if focus_position < 0 or (
                focus_maximum > 0 and focus_position > focus_maximum
            ):
                raise ValueError(
                    f"Focus position {focus_position} is outside "
                    f"0..{focus_maximum}."
                )

        flippers = values.get("flipper_positions", {})
        if not isinstance(flippers, dict):
            raise TypeError("flipper_positions must be a dictionary.")
        valid_flippers = dict(getattr(capabilities, "flipper_mirrors", {}))
        for key, value in flippers.items():
            flipper = int(key)
            position = int(value)
            if flipper not in valid_flippers:
                raise ValueError(f"Kymera flipper {flipper} is not present.")
            if position not in valid_flippers[flipper]:
                raise ValueError(
                    f"Invalid position {position} for Kymera flipper {flipper}."
                )

        detector_offset = values.get("detector_offset")
        if detector_offset is not None:
            if not isinstance(detector_offset, dict):
                raise TypeError("detector_offset must be a dictionary.")
            unknown = set(detector_offset) - {
                "entrance_port",
                "exit_port",
                "offset",
            }
            if unknown:
                raise ValueError(
                    f"Unknown detector-offset setting(s): {sorted(unknown)}"
                )
            if "offset" not in detector_offset:
                raise ValueError("detector_offset requires an offset value.")
            for port_name in ("entrance_port", "exit_port"):
                if int(detector_offset.get(port_name, 0)) not in {0, 1}:
                    raise ValueError(
                        f"Kymera {port_name} must be port 0 or port 1."
                    )

    def apply_user_settings(self, values: dict[str, Any]) -> None:
        """Apply capability-driven GUI settings, then refresh wavelength calibration."""

        if not isinstance(values, dict):
            raise TypeError("Andor settings must be supplied as a dictionary.")
        camera_values = values.get("camera", {})
        spectrograph_values = values.get("spectrograph", {})
        if not isinstance(camera_values, dict) or not isinstance(spectrograph_values, dict):
            raise TypeError("Andor camera/spectrograph settings must be dictionaries.")

        camera_setting_names = {item.name for item in fields(AndorCameraSettings)}
        unknown = set(camera_values) - camera_setting_names
        if unknown:
            raise ValueError(f"Unknown Andor camera setting(s): {sorted(unknown)}")
        valid_spectrograph_settings = {
            "grating",
            "center_wavelength_nm",
            "filter_position",
            "flipper_positions",
            "focus_mirror_position",
            "grating_offset",
            "detector_offset",
        }
        unknown = set(spectrograph_values) - valid_spectrograph_settings
        if unknown:
            raise ValueError(
                f"Unknown Kymera setting(s): {sorted(unknown)}"
            )

        try:
            self._validate_spectrograph_settings(spectrograph_values)
            if camera_values:
                updated = replace(
                    self.camera.settings,
                    **{key: int(value) for key, value in camera_values.items()},
                )
                self.camera.apply_settings(updated)

            if "grating" in spectrograph_values:
                self.spectrograph.set_grating(int(spectrograph_values["grating"]))
            if "center_wavelength_nm" in spectrograph_values:
                self.spectrograph.set_wavelength_nm(
                    float(spectrograph_values["center_wavelength_nm"])
                )
            if "filter_position" in spectrograph_values:
                self.spectrograph.set_filter_position(
                    int(spectrograph_values["filter_position"])
                )
            flipper_values = spectrograph_values.get("flipper_positions", {})
            for key, value in flipper_values.items():
                self.spectrograph.set_flipper_position(int(key), int(value))
            if "focus_mirror_position" in spectrograph_values:
                self.spectrograph.set_focus_mirror_position(
                    int(spectrograph_values["focus_mirror_position"])
                )
            if "grating_offset" in spectrograph_values:
                current_grating = int(
                    spectrograph_values.get(
                        "grating",
                        self.spectrograph.state()["grating"],
                    )
                )
                self.spectrograph.set_grating_offset(
                    current_grating,
                    int(spectrograph_values["grating_offset"]),
                )
            detector_offset = spectrograph_values.get("detector_offset")
            if detector_offset is not None:
                self.spectrograph.set_detector_offset(
                    int(detector_offset.get("entrance_port", 0)),
                    int(detector_offset.get("exit_port", 0)),
                    int(detector_offset["offset"]),
                )

            self.max_intensity = float(self.camera.max_intensity)
            self._configure_calibration_geometry()
        except (AndorSDK2Error, AndorSpectrographError, OSError) as exc:
            raise self._normalize_operation_error(
                "settings update",
                exc,
            ) from exc

    def acquire_spectrum(
        self,
        *,
        integration_ms: int,
        averages: int,
        correct_dark: bool,
        correct_nonlinearity: bool,
        averaging_mode: str = "software",
    ) -> SpectralAcquisition:
        del correct_dark, correct_nonlinearity
        if str(averaging_mode) not in {"software", "device"}:
            raise ValueError(f"Unknown averaging mode: {averaging_mode!r}")
        try:
            values = np.asarray(
                self.camera.acquire(float(integration_ms), max(1, int(averages))),
                dtype=float,
            )
        except (AndorSDK2Error, AndorSpectrographError, OSError) as exc:
            raise self._normalize_operation_error(
                "spectrum acquisition",
                exc,
            ) from exc
        if values.shape != self.wavelengths_nm.shape:
            raise AndorCommunicationError(
                "Andor camera data and Kymera calibration have different shapes: "
                f"{values.shape} != {self.wavelengths_nm.shape}."
            )
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            raise RuntimeError("Andor camera returned no finite intensity values.")
        return SpectralAcquisition(
            wavelengths_nm=self.wavelengths_nm.copy(),
            intensities_counts=values,
            signal_max_counts=float(np.max(finite)),
            device_averaging_used=False,
        )

    def get_ccd_temperature_c(self) -> float:
        try:
            return float(self.camera.get_temperature_c())
        except (AndorSDK2Error, OSError) as exc:
            raise self._normalize_operation_error(
                "temperature query",
                exc,
            ) from exc

    def set_tec_target_c(self, temperature_c: float) -> None:
        try:
            self.camera.set_temperature_c(float(temperature_c))
        except (AndorSDK2Error, OSError) as exc:
            raise self._normalize_operation_error(
                "temperature update",
                exc,
            ) from exc

    def set_tec_enabled(self, enabled: bool) -> None:
        try:
            self.camera.set_cooler_enabled(bool(enabled))
        except (AndorSDK2Error, OSError) as exc:
            raise self._normalize_operation_error(
                "cooler update",
                exc,
            ) from exc

    def close(self) -> None:
        spectrograph = getattr(self, "spectrograph", None)
        camera = getattr(self, "camera", None)
        self.spectrograph = None
        self.camera = None
        if spectrograph is not None:
            try:
                spectrograph.close()
            except Exception:
                pass
        if camera is not None:
            try:
                camera.close()
            except Exception:
                pass
