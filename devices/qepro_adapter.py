from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn

import numpy as np

from core.records import SpectralAcquisition, SpectrometerCapabilities
from devices.errors import SpectrometerCommandError, SpectrometerCommunicationError


class QEProSpectrometer:
    """Thin, validated adapter around python-seabreeze's Spectrometer API."""

    DEFAULT_MIN_INTEGRATION_US = 8_000
    DEFAULT_MAX_INTEGRATION_US = 60_000_000
    DEFAULT_TEC_TARGET_MIN_C = -25.0
    DEFAULT_TEC_TARGET_MAX_C = 40.0
    VALID_AVERAGING_MODES = {"software", "device"}

    def __init__(self, serial_number: str | None = None) -> None:
        from seabreeze.spectrometers import Spectrometer

        requested_serial = str(serial_number or "").strip()
        self.spec = (
            Spectrometer.from_serial_number(requested_serial)
            if requested_serial
            else Spectrometer.from_first_available()
        )
        self.wavelengths_nm = np.asarray(self.spec.wavelengths(), dtype=float)
        if self.wavelengths_nm.ndim != 1 or self.wavelengths_nm.size == 0:
            raise RuntimeError("QEPro returned an invalid wavelength array.")
        if not np.all(np.isfinite(self.wavelengths_nm)):
            raise RuntimeError("QEPro wavelength array contains non-finite values.")

        self.name = str(
            self._read_attr_or_method(self.spec, "model", type(self.spec).__name__)
        )
        self.serial_number = str(
            self._read_attr_or_method(self.spec, "serial_number", "")
        )
        self.max_intensity = self._coerce_float(
            self._read_attr_or_method(self.spec, "max_intensity", 65535.0),
            65535.0,
        )

        self._capabilities_cache: SpectrometerCapabilities | None = None
        self._hardware_average_method_checked = False
        self._hardware_average_method: Callable[[int], Any] | None = None
        self._applied_integration_us: int | None = None
        self._applied_device_averages: int | None = None
        self.tec_target_min_c = self.DEFAULT_TEC_TARGET_MIN_C
        self.tec_target_max_c = self.DEFAULT_TEC_TARGET_MAX_C

    def _read_attr_or_method(
        self,
        obj: object,
        name: str,
        default: Any = None,
    ) -> Any:
        try:
            value = getattr(obj, name, default)
        except Exception as exc:
            self._raise_if_transport_error(f"{name} query", exc)
        if callable(value):
            try:
                return value()
            except Exception as exc:
                if self._is_transport_error(exc):
                    self._raise_if_transport_error(f"{name} query", exc)
                return default
        return value

    @staticmethod
    def _is_transport_error(exc: Exception) -> bool:
        type_names = {
            cls.__name__.lower()
            for cls in type(exc).__mro__
        }
        message = str(exc).lower()
        return bool(
            "seabreezeerror" in type_names
            or "usberror" in type_names
            or any(
                token in message
                for token in (
                    "data transfer error",
                    "device not found",
                    "no device",
                    "not connected",
                    "disconnected",
                    "libusb",
                    "usb communication",
                    "usb transfer",
                    "usb read",
                    "usb write",
                    "endpoint",
                    "bulk transfer",
                )
            )
        )

    @staticmethod
    def _raise_if_transport_error(
        operation: str,
        exc: Exception,
    ) -> NoReturn:
        message = str(exc)
        if QEProSpectrometer._is_transport_error(exc):
            raise SpectrometerCommunicationError(
                f"QEPro communication failed during "
                f"{operation}: {message}"
            ) from exc

        raise exc

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return float(default)
        return result if np.isfinite(result) else float(default)

    def _features(self) -> dict[str, list[object]]:
        try:
            features = self.spec.features
        except Exception as exc:
            if self._is_transport_error(exc):
                self._raise_if_transport_error("feature discovery", exc)
            return {}
        return dict(features or {})

    def _feature(self, name: str) -> object | None:
        try:
            accessor = getattr(self.spec, "f", None)
        except Exception as exc:
            self._raise_if_transport_error("feature accessor query", exc)
        if accessor is not None:
            try:
                feature = getattr(accessor, name)
            except (AttributeError, KeyError):
                feature = None
            except Exception as exc:
                if self._is_transport_error(exc):
                    self._raise_if_transport_error(f"{name} feature access", exc)
                feature = None
            if feature is not None:
                return feature

        feature_list = self._features().get(name, [])
        return feature_list[0] if feature_list else None

    def _feature_objects(self) -> list[object]:
        # Some backends expose processing methods directly on Spectrometer,
        # while others expose them through feature objects.
        objects: list[object] = [self.spec]
        objects.extend(
            item
            for values in self._features().values()
            if values
            for item in values
        )
        return objects

    def _feature_method_report(self) -> dict[str, list[str]]:
        report: dict[str, list[str]] = {}
        for name, feature_list in self._features().items():
            if not feature_list:
                continue

            methods: set[str] = set()
            for feature in feature_list:
                for attribute in dir(feature):
                    if attribute.startswith("_"):
                        continue
                    try:
                        value = getattr(feature, attribute)
                    except Exception as exc:
                        if self._is_transport_error(exc):
                            self._raise_if_transport_error(
                                f"{name} capability discovery",
                                exc,
                            )
                        continue
                    if callable(value):
                        methods.add(attribute)
            report[str(name)] = sorted(methods)
        return report

    def _find_hardware_average_method(self) -> Callable[[int], Any] | None:
        if self._hardware_average_method_checked:
            return self._hardware_average_method

        self._hardware_average_method_checked = True
        exact_names = (
            "set_scans_to_average",
            "scans_to_average",
            "set_scans_to_average_count",
            "set_number_of_scans_to_average",
            "set_spectrum_processing_scans_to_average",
        )

        for feature in self._feature_objects():
            for name in exact_names:
                try:
                    method = getattr(feature, name, None)
                except Exception as exc:
                    if self._is_transport_error(exc):
                        self._raise_if_transport_error(
                            "hardware averaging discovery",
                            exc,
                        )
                    continue
                if callable(method):
                    self._hardware_average_method = method
                    return method

        # Restrict fuzzy matching to setter-looking method names.
        for feature in self._feature_objects():
            for name in dir(feature):
                lowered = name.lower()
                if "scan" not in lowered or "average" not in lowered:
                    continue
                if not (lowered.startswith("set") or "set_" in lowered):
                    continue
                try:
                    method = getattr(feature, name, None)
                except Exception as exc:
                    if self._is_transport_error(exc):
                        self._raise_if_transport_error(
                            "hardware averaging discovery",
                            exc,
                        )
                    continue
                if callable(method):
                    self._hardware_average_method = method
                    return method

        return None

    def capabilities(self, *, refresh: bool = False) -> SpectrometerCapabilities:
        if self._capabilities_cache is not None and not refresh:
            return self._capabilities_cache

        feature_methods = self._feature_method_report()
        min_us, max_us = self._integration_limits_us()
        capabilities = SpectrometerCapabilities(
            model=str(self._read_attr_or_method(self.spec, "model", self.name)),
            serial_number=str(
                self._read_attr_or_method(self.spec, "serial_number", self.serial_number)
            ),
            pixels=int(
                self._read_attr_or_method(self.spec, "pixels", len(self.wavelengths_nm))
                or len(self.wavelengths_nm)
            ),
            max_intensity=self._coerce_float(
                self._read_attr_or_method(self.spec, "max_intensity", self.max_intensity),
                self.max_intensity,
            ),
            integration_time_min_us=min_us,
            integration_time_max_us=max_us,
            features=sorted(feature_methods),
            feature_methods=feature_methods,
            tec_supported=self._feature("thermo_electric") is not None,
            device_averaging_supported=self._find_hardware_average_method() is not None,
        )
        self._capabilities_cache = capabilities
        return capabilities

    def _thermo(self) -> object:
        feature = self._feature("thermo_electric")
        if feature is None:
            raise RuntimeError("No thermo_electric feature is available.")
        return feature

    def get_ccd_temperature_c(self) -> float:
        try:
            temperature_c = float(
                self._thermo().read_temperature_degrees_celsius()
            )
        except Exception as exc:
            self._raise_if_transport_error("CCD temperature readout", exc)

        if not np.isfinite(temperature_c):
            raise RuntimeError("QEPro returned a non-finite CCD temperature.")
        return temperature_c

    def _validate_tec_target_c(self, temperature_c: float) -> float:
        value = float(temperature_c)
        if not np.isfinite(value):
            raise ValueError("TEC target must be finite.")
        minimum_c = float(
            getattr(self, "tec_target_min_c", self.DEFAULT_TEC_TARGET_MIN_C)
        )
        maximum_c = float(
            getattr(self, "tec_target_max_c", self.DEFAULT_TEC_TARGET_MAX_C)
        )
        if not np.isfinite(minimum_c) or not np.isfinite(maximum_c):
            raise RuntimeError("QEPro TEC guard limits must be finite.")
        if minimum_c > maximum_c:
            raise RuntimeError("QEPro TEC guard minimum exceeds its maximum.")
        if not minimum_c <= value <= maximum_c:
            raise ValueError(
                f"TEC target {value:.2f} °C is outside the configured QEPro "
                f"guard range [{minimum_c:.2f}, {maximum_c:.2f}] °C."
            )
        return value

    @staticmethod
    def _read_tec_temperature_for_health_check(thermo: object) -> float:
        temperature_c = float(thermo.read_temperature_degrees_celsius())
        if not np.isfinite(temperature_c):
            raise RuntimeError(
                "QEPro returned a non-finite CCD temperature during health check."
            )
        return temperature_c

    def set_tec_target_c(self, temperature_c: float) -> None:
        target_c = self._validate_tec_target_c(temperature_c)
        thermo = self._thermo()
        try:
            thermo.set_temperature_setpoint_degrees_celsius(target_c)
        except Exception as exc:
            if not self._is_transport_error(exc):
                raise

            # SeaBreeze can report a transfer failure for a rejected target
            # while the QEPro remains reachable. One read-only health check
            # distinguishes a command rejection from a lost USB connection.
            try:
                readback_c = self._read_tec_temperature_for_health_check(thermo)
            except Exception as health_exc:
                raise SpectrometerCommunicationError(
                    "QEPro communication failed during TEC setpoint "
                    f"configuration: {exc}. Follow-up CCD temperature "
                    f"readout also failed: {health_exc}"
                ) from exc

            raise SpectrometerCommandError(
                f"QEPro rejected TEC target {target_c:.2f} °C, but CCD "
                f"temperature readback succeeded at {readback_c:.2f} °C; "
                f"the spectrometer remains connected. Original error: {exc}"
            ) from exc

    def set_tec_enabled(self, enabled: bool) -> None:
        thermo = self._thermo()
        state = "on" if enabled else "off"
        try:
            try:
                thermo.enable_tec(state)
            except (TypeError, ValueError):
                thermo.enable_tec(bool(enabled))
        except Exception as exc:
            self._raise_if_transport_error("TEC state configuration", exc)

    def set_hardware_averages(self, averages: int) -> bool:
        method = self._find_hardware_average_method()
        if method is None:
            return False
        value = max(1, int(averages))
        if self._applied_device_averages == value:
            return True
        try:
            method(value)
        except Exception as exc:
            self._raise_if_transport_error("hardware averaging configuration", exc)
        self._applied_device_averages = value
        return True

    def _integration_limits_us(self) -> tuple[int, int]:
        try:
            limits = getattr(self.spec, "integration_time_micros_limits", None)
            min_us, max_us = limits() if callable(limits) else limits
            min_us = int(min_us)
            max_us = int(max_us)
            if min_us <= 0 or max_us < min_us:
                raise ValueError("invalid integration limits")
            return min_us, max_us
        except Exception as exc:
            if self._is_transport_error(exc):
                self._raise_if_transport_error("integration-limit query", exc)
            return self.DEFAULT_MIN_INTEGRATION_US, self.DEFAULT_MAX_INTEGRATION_US

    def _validate_integration_us(self, integration_us: int) -> int:
        value = int(integration_us)
        min_us, max_us = self._integration_limits_us()
        if not min_us <= value <= max_us:
            raise ValueError(
                f"Integration time {value} us is outside spectrometer range "
                f"[{min_us}, {max_us}] us"
            )
        return value

    def _set_integration_time_us(self, integration_us: int) -> bool:
        """Apply a changed integration time and report whether hardware changed."""
        if self._applied_integration_us == integration_us:
            return False
        try:
            self.spec.integration_time_micros(integration_us)
        except Exception as exc:
            self._raise_if_transport_error(
                "integration-time configuration",
                exc,
            )
        self._applied_integration_us = integration_us
        return True

    def _read_intensities(
        self,
        *,
        correct_dark: bool,
        correct_nonlinearity: bool,
    ) -> np.ndarray:
        try:
            values = np.asarray(
                self.spec.intensities(
                    correct_dark_counts=correct_dark,
                    correct_nonlinearity=correct_nonlinearity,
                ),
                dtype=float,
            )
        except Exception as exc:
            self._raise_if_transport_error(
                "spectrum readout",
                exc,
            )
        if values.ndim != 1:
            raise RuntimeError("QEPro intensity data must be one-dimensional.")
        if values.shape != self.wavelengths_nm.shape:
            raise RuntimeError(
                "QEPro intensity and wavelength arrays have different shapes: "
                f"{values.shape} != {self.wavelengths_nm.shape}"
            )
        return values

    @staticmethod
    def _signal_max(values: np.ndarray) -> float:
        finite = np.asarray(values, dtype=float)
        finite = finite[np.isfinite(finite)]
        if finite.size == 0:
            raise RuntimeError("QEPro returned no finite intensity values.")
        return float(np.max(finite))

    def acquire_spectrum(
        self,
        *,
        integration_ms: int,
        averages: int,
        correct_dark: bool,
        correct_nonlinearity: bool,
        averaging_mode: str = "software",
    ) -> SpectralAcquisition:
        mode = str(averaging_mode).strip().lower()
        if mode not in self.VALID_AVERAGING_MODES:
            raise ValueError(
                f"Unknown averaging mode {averaging_mode!r}; expected "
                f"one of {sorted(self.VALID_AVERAGING_MODES)}"
            )

        averages = max(1, int(averages))
        integration_us = self._validate_integration_us(int(integration_ms) * 1000)
        integration_changed = self._set_integration_time_us(integration_us)
        previous_device_averages = self._applied_device_averages

        device_averaging_used = False
        if mode == "device":
            try:
                device_averaging_used = self.set_hardware_averages(averages)
            except SpectrometerCommunicationError:
                raise
            except Exception:
                device_averaging_used = False
                try:
                    self.set_hardware_averages(1)
                except SpectrometerCommunicationError:
                    raise
                except Exception:
                    pass
        else:
            # A previous device-averaged acquisition may have left the backend
            # configured for N scans. Reset to one before Python-side averaging.
            try:
                self.set_hardware_averages(1)
            except SpectrometerCommunicationError:
                raise
            except Exception:
                pass

        device_averaging_changed = (
            self._applied_device_averages is not None
            and self._applied_device_averages != previous_device_averages
        )
        if integration_changed or device_averaging_changed:
            # QEPro/SeaBreeze can return the previously completed frame on the
            # first read after a configuration change. Discard exactly one.
            self._read_intensities(
                correct_dark=correct_dark,
                correct_nonlinearity=correct_nonlinearity,
            )

        if device_averaging_used:
            values = self._read_intensities(
                correct_dark=correct_dark,
                correct_nonlinearity=correct_nonlinearity,
            )
            return SpectralAcquisition(
                wavelengths_nm=self.wavelengths_nm.copy(),
                intensities_counts=values,
                signal_max_counts=self._signal_max(values),
                device_averaging_used=True,
            )

        running_mean: np.ndarray | None = None
        signal_max = float("-inf")
        for index in range(averages):
            values = self._read_intensities(
                correct_dark=correct_dark,
                correct_nonlinearity=correct_nonlinearity,
            )
            signal_max = max(signal_max, self._signal_max(values))
            if running_mean is None:
                running_mean = values.astype(float, copy=True)
            else:
                running_mean += (values - running_mean) / float(index + 1)

        if running_mean is None:
            raise RuntimeError("No spectra were acquired.")

        return SpectralAcquisition(
            wavelengths_nm=self.wavelengths_nm.copy(),
            intensities_counts=running_mean,
            signal_max_counts=signal_max,
            device_averaging_used=False,
        )

    def close(self) -> None:
        close = getattr(self.spec, "close", None)
        if callable(close):
            close()
