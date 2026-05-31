from __future__ import annotations

import numpy as np

from core.records import SpectrometerCapabilities


class QEProSpectrometer:
    def __init__(self) -> None:
        from seabreeze.spectrometers import Spectrometer

        self.spec = Spectrometer.from_first_available()
        self.wavelengths_nm = np.asarray(self.spec.wavelengths(), dtype=float)

        self.name = str(getattr(self.spec, "model", type(self.spec).__name__))
        self.serial_number = str(getattr(self.spec, "serial_number", ""))
        self.max_intensity = float(getattr(self.spec, "max_intensity", 65535.0))

    def _feature_objects(self) -> list[object]:
        out = []

        try:
            features = self.spec.features
        except Exception:
            return out

        for feature_list in features.values():
            if not feature_list:
                continue
            out.extend(feature_list)

        return out

    def _feature_method_report(self) -> dict[str, list[str]]:
        report: dict[str, list[str]] = {}

        try:
            features = self.spec.features
        except Exception:
            return report

        for name, feature_list in features.items():
            methods = []

            for obj in feature_list or []:
                for attr in dir(obj):
                    if attr.startswith("_"):
                        continue

                    value = getattr(obj, attr, None)

                    if callable(value):
                        methods.append(attr)

            report[str(name)] = sorted(set(methods))

        return report

    def _find_first_method(self, candidate_names: list[str]):
        candidate_names_lower = [x.lower() for x in candidate_names]

        for obj in self._feature_objects():
            for attr in dir(obj):
                if attr.lower() not in candidate_names_lower:
                    continue

                method = getattr(obj, attr, None)

                if callable(method):
                    return method

        return None

    def _find_fuzzy_method(self, required_terms: list[str], excluded_terms: list[str] | None = None):
        excluded_terms = excluded_terms or []

        for obj in self._feature_objects():
            for attr in dir(obj):
                if attr.startswith("_"):
                    continue

                name = attr.lower()

                if not all(term.lower() in name for term in required_terms):
                    continue

                if any(term.lower() in name for term in excluded_terms):
                    continue

                method = getattr(obj, attr, None)

                if callable(method):
                    return method

        return None

    def capabilities(self) -> SpectrometerCapabilities:
        feature_methods = self._feature_method_report()

        feature_names = [
            name for name, methods in feature_methods.items()
            if methods
        ]

        try:
            min_us, max_us = self.spec.integration_time_micros_limits
        except Exception:
            min_us, max_us = 0, 0

        tec_supported = self._tec_get_temperature_method() is not None
        device_averaging_supported = self._set_hardware_average_method() is not None

        return SpectrometerCapabilities(
            model=self.name,
            serial_number=self.serial_number,
            pixels=int(getattr(self.spec, "pixels", len(self.wavelengths_nm))),
            max_intensity=float(self.max_intensity),
            integration_time_min_us=int(min_us),
            integration_time_max_us=int(max_us),
            features=sorted(feature_names),
            feature_methods=feature_methods,
            tec_supported=bool(tec_supported),
            device_averaging_supported=bool(device_averaging_supported),
        )

    # ---------- TEC probing ----------

    def _tec_get_temperature_method(self):
        return (
            self._find_first_method(
                [
                    "get_temperature_degrees_celsius",
                    "read_temperature_degrees_celsius",
                    "get_temperature_celsius",
                    "read_temperature_celsius",
                    "get_temperature",
                    "read_temperature",
                    "get_tec_temperature",
                    "get_tec_temperature_degrees_celsius",
                ]
            )
            or self._find_fuzzy_method(["temperature"], excluded_terms=["set"])
        )

    def _tec_set_target_method(self):
        return (
            self._find_first_method(
                [
                    "set_temperature_setpoint_degrees_celsius",
                    "set_temperature_setpoint_celsius",
                    "set_temperature_setpoint",
                    "set_tec_temperature_degrees_celsius",
                    "set_tec_temperature",
                    "set_temperature",
                ]
            )
            or self._find_fuzzy_method(["set", "temperature"])
        )

    def _tec_enable_method(self):
        return (
            self._find_first_method(
                [
                    "set_tec_enable",
                    "set_tec_enabled",
                    "set_thermo_electric_enable",
                    "set_thermo_electric_enabled",
                    "set_enable",
                ]
            )
            or self._find_fuzzy_method(["enable"], excluded_terms=["get"])
        )

    def get_ccd_temperature_c(self) -> float:
        method = self._tec_get_temperature_method()

        if method is None:
            raise RuntimeError("No QEPro TEC/CCD temperature read method was found.")

        return float(method())

    def set_tec_target_c(self, temperature_c: float) -> None:
        method = self._tec_set_target_method()

        if method is None:
            raise RuntimeError("No QEPro TEC target-temperature set method was found.")

        method(float(temperature_c))

    def set_tec_enabled(self, enabled: bool) -> None:
        method = self._tec_enable_method()

        if method is None:
            raise RuntimeError("No QEPro TEC enable/disable method was found.")

        method(bool(enabled))

    # ---------- hardware averaging probing ----------

    def _set_hardware_average_method(self):
        return (
            self._find_first_method(
                [
                    "set_scans_to_average",
                    "scans_to_average",
                    "set_scans_to_average_count",
                    "set_number_of_scans_to_average",
                    "set_spectrum_processing_scans_to_average",
                ]
            )
            or self._find_fuzzy_method(["scan", "average"])
        )

    def set_hardware_averages(self, averages: int) -> bool:
        method = self._set_hardware_average_method()

        if method is None:
            return False

        method(int(max(1, averages)))
        return True

    def acquire_spectrum(
        self,
        *,
        integration_ms: int,
        averages: int,
        correct_dark: bool,
        correct_nonlinearity: bool,
        averaging_mode: str = "software",
    ) -> tuple[np.ndarray, np.ndarray, float, bool]:
        averages = max(1, int(averages))
        integration_us = max(1, int(integration_ms * 1000))

        self.spec.integration_time_micros(integration_us)

        device_averaging_used = False

        if averaging_mode == "device":
            try:
                device_averaging_used = self.set_hardware_averages(averages)
            except Exception:
                device_averaging_used = False

        if device_averaging_used:
            y = np.asarray(
                self.spec.intensities(
                    correct_dark_counts=bool(correct_dark),
                    correct_nonlinearity=bool(correct_nonlinearity),
                ),
                dtype=float,
            )

            return (
                self.wavelengths_nm.copy(),
                y,
                float(np.nanmax(y)),
                True,
            )

        traces = []
        signal_max_counts = float("-inf")

        for _ in range(averages):
            y = np.asarray(
                self.spec.intensities(
                    correct_dark_counts=bool(correct_dark),
                    correct_nonlinearity=bool(correct_nonlinearity),
                ),
                dtype=float,
            )

            signal_max_counts = max(signal_max_counts, float(np.nanmax(y)))
            traces.append(y)

        return (
            self.wavelengths_nm.copy(),
            np.mean(np.vstack(traces), axis=0),
            signal_max_counts,
            False,
        )

    def close(self) -> None:
        close = getattr(self.spec, "close", None)
        if callable(close):
            close()
