from __future__ import annotations

import math
import time
from collections.abc import Callable
from copy import deepcopy
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

    ADAPTER_VERSION = "2026.09.14"

    def __init__(
        self,
        solis_dir: str | Path,
        *,
        camera_index: int = 0,
        spectrograph_index: int = 0,
        camera_dll: str | Path | None = None,
        camera=None,
        spectrograph=None,
    ) -> None:
        self.solis_dir = Path(solis_dir)
        self.camera = camera
        self.spectrograph = spectrograph
        try:
            if self.spectrograph is None:
                self.spectrograph = AndorKymera(
                    self.solis_dir,
                    device_index=int(spectrograph_index),
                )
            if self.camera is None:
                try:
                    self.camera = AndorSDK2Camera(
                        self.solis_dir,
                        camera_index=int(camera_index),
                        camera_dll=camera_dll,
                    )
                except Exception as exc:
                    serial = str(
                        getattr(
                            getattr(self.spectrograph, "capabilities", None),
                            "serial_number",
                            "unknown",
                        )
                    )
                    raise AndorSDK2Error(
                        f"Kymera {serial} connected successfully, but the iDus "
                        f"camera connection failed: {exc}"
                    ) from exc
            self.name = (
                f"Andor {self.camera.capabilities.model} + Kymera "
                f"{self.spectrograph.capabilities.serial_number}"
            )
            self.serial_number = str(self.camera.capabilities.serial_number)
            self.max_intensity = float(self.camera.max_intensity)
            self._configure_calibration_geometry()
            self._metadata_state = self._read_metadata_state()
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
        selected_camera_dll = getattr(self.camera, "dll_path", None)
        if selected_camera_dll is not None:
            camera_schema["sdk2_dll"] = str(selected_camera_dll)
        temperature_target_c = getattr(self.camera, "temperature_target_c", None)
        if temperature_target_c is not None:
            camera_schema["temperature_target_c"] = float(temperature_target_c)
        spectrograph_schema = self.spectrograph.capabilities.as_control_schema()
        state = self.user_state(refresh=True)
        return SpectrometerCapabilities(
            model=self.name,
            serial_number=self.serial_number,
            pixels=int(self.camera.output_pixel_count),
            max_intensity=self.max_intensity,
            # SDK2 may apply an exposure shorter than the single-scan frame
            # period in Run-Till-Abort mode. Accept positive millisecond GUI
            # requests and persist both requested and SDK-applied values.
            integration_time_min_us=1_000,
            integration_time_max_us=max(
                1,
                int(
                    math.floor(
                        float(
                            getattr(
                                self.camera.capabilities,
                                "maximum_exposure_ms",
                                3_600_000.0,
                            )
                        )
                        * 1000.0
                    )
                ),
            ),
            features=[
                "andor_sdk2_camera",
                "kymera_spectrograph",
                "full_vertical_binning",
                "software_averaging",
                "run_till_abort_streaming",
            ],
            feature_methods={
                "andor_camera": [
                    "AD channel",
                    "output amplifier",
                    "horizontal/vertical readout speed",
                    "preamp gain",
                    "verified full-vertical-binning geometry",
                    "cooler/temperature",
                    "continuous circular-buffer acquisition",
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
                "state": state,
                "step_and_glue": {
                    "supported": False,
                    "reason": (
                        "Step-and-Glue is a software scan/merge workflow and will be "
                        "enabled after single-frame hardware calibration is verified."
                    ),
                },
            },
        )

    def user_state(self, *, refresh: bool = False) -> dict[str, Any]:
        if refresh or not hasattr(self, "_metadata_state"):
            self._metadata_state = self._read_metadata_state()
        return deepcopy(self._metadata_state)

    def _read_metadata_state(self) -> dict[str, Any]:
        return {
            "camera": asdict(self.camera.settings),
            "spectrograph": self.spectrograph.state(),
        }

    def metadata_snapshot(self) -> dict[str, object]:
        """Return the last readback-confirmed state without per-frame I/O."""

        state = self.user_state()
        camera = dict(state.get("camera", {}))
        spectrograph = dict(state.get("spectrograph", {}))
        ad_channel = int(camera.get("ad_channel", 0))
        amplifier = int(camera.get("output_amplifier", 0))
        horizontal_index = int(camera.get("horizontal_speed_index", 0))
        vertical_index = int(camera.get("vertical_speed_index", 0))
        gain_index = int(camera.get("preamp_gain_index", 0))
        horizontal_values = dict(
            getattr(self.camera.capabilities, "horizontal_speeds_mhz", {})
        ).get(f"{ad_channel}:{amplifier}", ())
        vertical_values = tuple(
            getattr(self.camera.capabilities, "vertical_speeds_us", ())
        )
        gain_values = tuple(getattr(self.camera.capabilities, "preamp_gains", ()))
        result: dict[str, object] = {
            "grating": spectrograph.get("grating"),
            "center_wavelength_nm": spectrograph.get("center_wavelength_nm"),
            "output_amplifier": amplifier,
            "horizontal_readout_mhz": (
                horizontal_values[horizontal_index]
                if 0 <= horizontal_index < len(horizontal_values)
                else None
            ),
            "vertical_shift_us": (
                vertical_values[vertical_index]
                if 0 <= vertical_index < len(vertical_values)
                else None
            ),
            "preamp_gain": (
                gain_values[gain_index]
                if 0 <= gain_index < len(gain_values)
                else None
            ),
            "horizontal_binning": int(camera.get("horizontal_binning", 1)),
        }
        for name in ("filter_position", "focus_mirror_position"):
            if name in spectrograph:
                result[name] = spectrograph[name]
        # Ports are optional provenance, as requested. Include them only when
        # the installed Kymera reports flipper hardware.
        flippers = spectrograph.get("flipper_positions")
        if flippers:
            result["flipper_positions"] = dict(flippers)

        current_grating = int(spectrograph.get("grating", 0) or 0)
        grating = next(
            (
                item
                for item in getattr(self.spectrograph.capabilities, "gratings", ())
                if int(item.index) == current_grating
            ),
            None,
        )
        if grating is not None:
            result["grating_offset"] = int(grating.offset)
        detector_offsets = dict(
            getattr(self.spectrograph.capabilities, "detector_offsets", {})
        )
        if detector_offsets:
            result["detector_offsets"] = detector_offsets
        return {key: value for key, value in result.items() if value is not None}

    def software_provenance(self) -> dict[str, str]:
        dll_path = getattr(self.camera, "dll_path", None)
        return {
            "adapter": "andor_sdk2_kymera",
            "adapter_version": self.ADAPTER_VERSION,
            "vendor_runtime": Path(dll_path).name if dll_path is not None else "SDK2",
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

    @staticmethod
    def _spectrograph_readback_mismatches(
        requested: dict[str, Any],
        actual: dict[str, Any],
    ) -> list[str]:
        mismatches: list[str] = []
        for name in ("grating", "filter_position", "focus_mirror_position"):
            if name not in requested:
                continue
            actual_value = actual.get(name)
            try:
                matches = int(actual_value) == int(requested[name])
            except (TypeError, ValueError):
                matches = False
            if not matches:
                mismatches.append(
                    f"{name} requested {int(requested[name])}, "
                    f"read back {actual_value if actual_value is not None else 'unavailable'}"
                )
        if "center_wavelength_nm" in requested:
            requested_nm = float(requested["center_wavelength_nm"])
            try:
                actual_nm = float(actual.get("center_wavelength_nm", float("nan")))
            except (TypeError, ValueError):
                actual_nm = float("nan")
            if not math.isfinite(actual_nm) or not math.isclose(
                actual_nm,
                requested_nm,
                rel_tol=0.0,
                abs_tol=0.05,
            ):
                mismatches.append(
                    f"center_wavelength_nm requested {requested_nm:g}, "
                    f"read back {actual_nm:g}"
                )
        requested_flippers = dict(requested.get("flipper_positions", {}))
        actual_flippers = dict(actual.get("flipper_positions", {}))
        for key, value in requested_flippers.items():
            actual_value = actual_flippers.get(str(key))
            try:
                matches = int(actual_value) == int(value)
            except (TypeError, ValueError):
                matches = False
            if not matches:
                mismatches.append(
                    f"flipper {key} requested {int(value)}, "
                    f"read back {actual_value if actual_value is not None else 'unavailable'}"
                )
        return mismatches

    def _wait_for_spectrograph_readback(
        self,
        requested: dict[str, Any],
        *,
        timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        """Wait for motorized settings and require their queried state to match."""

        deadline_s = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            actual = self.spectrograph.state()
            mismatches = self._spectrograph_readback_mismatches(requested, actual)
            if not mismatches:
                return actual
            if time.monotonic() >= deadline_s:
                raise AndorSpectrographError(
                    "Kymera did not reach the requested state within "
                    f"{float(timeout_s):g} s: " + "; ".join(mismatches)
                )
            time.sleep(0.05)

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
            # Configuration is only accepted while acquisition is idle. Stop
            # any retained Run-Till-Abort session before camera or Kymera
            # changes so the next measurement must re-arm with the new state.
            self.camera.stop_continuous_acquisition()
            if camera_values:
                updated = replace(
                    self.camera.settings,
                    **{key: int(value) for key, value in camera_values.items()},
                )
                if updated != self.camera.settings:
                    self.camera.apply_settings(updated)

            current_state = self.spectrograph.state()
            if "grating" in spectrograph_values:
                target_grating = int(spectrograph_values["grating"])
                if int(current_state.get("grating", -1)) != target_grating:
                    self.spectrograph.set_grating(target_grating)
                    # Wavelength limits and motion apply to the selected
                    # grating.  Do not issue the wavelength command while the
                    # turret is still reporting the previous grating.
                    current_state = self._wait_for_spectrograph_readback(
                        {"grating": target_grating}
                    )
            if "center_wavelength_nm" in spectrograph_values:
                target_center_nm = float(
                    spectrograph_values["center_wavelength_nm"]
                )
                current_center_nm = float(
                    current_state.get("center_wavelength_nm", float("nan"))
                )
                if not math.isclose(
                    current_center_nm,
                    target_center_nm,
                    rel_tol=0.0,
                    abs_tol=0.05,
                ):
                    self.spectrograph.set_wavelength_nm(target_center_nm)
                    current_state = self._wait_for_spectrograph_readback(
                        {"center_wavelength_nm": target_center_nm}
                    )
            if "filter_position" in spectrograph_values:
                target_filter = int(spectrograph_values["filter_position"])
                if int(current_state.get("filter_position", -1)) != target_filter:
                    self.spectrograph.set_filter_position(target_filter)
                    current_state = self._wait_for_spectrograph_readback(
                        {"filter_position": target_filter}
                    )
            flipper_values = spectrograph_values.get("flipper_positions", {})
            for key, value in flipper_values.items():
                target_flipper = int(value)
                current_flippers = dict(
                    current_state.get("flipper_positions", {})
                )
                if int(current_flippers.get(str(key), -1)) != target_flipper:
                    self.spectrograph.set_flipper_position(
                        int(key), target_flipper
                    )
                    current_state = self._wait_for_spectrograph_readback(
                        {"flipper_positions": {str(key): target_flipper}}
                    )
            if "focus_mirror_position" in spectrograph_values:
                target_focus = int(
                    spectrograph_values["focus_mirror_position"]
                )
                if int(current_state.get("focus_mirror_position", -1)) != target_focus:
                    self.spectrograph.set_focus_mirror_position(target_focus)
                    current_state = self._wait_for_spectrograph_readback(
                        {"focus_mirror_position": target_focus}
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

            # Kymera motion commands may return before the mechanism reaches
            # its requested position.  Do not refresh detector calibration or
            # report success until the queryable state agrees with the request.
            spectrograph_state = self._wait_for_spectrograph_readback(
                spectrograph_values
            )
            self.max_intensity = float(self.camera.max_intensity)
            self._configure_calibration_geometry()
            self._metadata_state = {
                "camera": asdict(self.camera.settings),
                "spectrograph": spectrograph_state,
            }
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
        continuous_session: str = "",
        continuous_target_midpoint_s: float = float("nan"),
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> SpectralAcquisition:
        del correct_dark, correct_nonlinearity
        if str(averaging_mode) not in {"software", "device"}:
            raise ValueError(f"Unknown averaging mode: {averaging_mode!r}")
        try:
            values = np.asarray(
                self.camera.acquire(
                    float(integration_ms),
                    max(1, int(averages)),
                    progress_callback=progress_callback,
                    continuous_session=str(continuous_session),
                    continuous_target_midpoint_s=float(
                        continuous_target_midpoint_s
                    ),
                ),
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
            timing=getattr(self.camera, "last_timing", None),
            applied_integration_ms=float(
                getattr(
                    getattr(self.camera, "last_acquisition_profile", None),
                    "applied_exposure_ms",
                    integration_ms,
                )
            ),
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

    def stop_continuous_acquisition(self) -> None:
        camera = getattr(self, "camera", None)
        if camera is None:
            return
        try:
            camera.stop_continuous_acquisition()
        except (AndorSDK2Error, OSError) as exc:
            raise self._normalize_operation_error(
                "continuous acquisition stop",
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
