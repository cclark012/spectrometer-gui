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

    def _read_attr_or_method(self, obj, name: str, default=None):
        value = getattr(obj, name, default)

        if callable(value):
            try:
                return value()
            except Exception:
                return default

        return value

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

    def _find_fuzzy_method(
            self, 
            required_terms: list[str], 
            excluded_terms: list[str] | None = None
        ):
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
        feature_methods = {}

        try:
            features = self.spec.features
            for name, feature_list in features.items():
                methods = []
                for feature_obj in feature_list or []:
                    for attr in dir(feature_obj):
                        if attr.startswith("_"):
                            continue
                        value = getattr(feature_obj, attr, None)
                        if callable(value):
                            methods.append(attr)
                feature_methods[str(name)] = sorted(set(methods))
        except Exception as exc:
            feature_methods["feature_probe_error"] = [repr(exc)]

        feature_names = sorted(feature_methods.keys())

        min_us = 0
        max_us = 0

        try:
            limits = getattr(self.spec, "integration_time_micros_limits", None)

            if callable(limits):
                min_us, max_us = limits()
            elif limits is not None:
                min_us, max_us = limits

        except Exception:
            min_us, max_us = 0, 0

        model = self._read_attr_or_method(self.spec, "model", self.name)
        serial = self._read_attr_or_method(self.spec, "serial_number", self.serial_number)
        pixels = self._read_attr_or_method(self.spec, "pixels", len(self.wavelengths_nm))
        max_intensity = self._read_attr_or_method(self.spec, "max_intensity", self.max_intensity)

        try:
            max_intensity = float(max_intensity)
        except Exception:
            max_intensity = float("nan")

        return SpectrometerCapabilities(
            model=str(model),
            serial_number=str(serial),
            pixels=int(pixels) if pixels is not None else len(self.wavelengths_nm),
            max_intensity=max_intensity,
            integration_time_min_us=int(min_us or 0),
            integration_time_max_us=int(max_us or 0),
            features=feature_names,
            feature_methods=feature_methods,
            tec_supported=bool(self._tec_get_temperature_method() is not None),
            device_averaging_supported=bool(self._set_hardware_average_method() is not None),
        )

    def _feature(self, name: str):
        accessor = getattr(self.spec, "f", None)

        if accessor is not None:
            feature = getattr(accessor, name, None)
            if feature is not None:
                return feature

        features = getattr(self.spec, "features", {})
        feature_list = features.get(name, [])

        if feature_list:
            return feature_list[0]

        return None

    def _thermo(self):
        return self._feature("thermo_electric")

    def get_ccd_temperature_c(self) -> float:
        thermo = self._thermo()

        if thermo is None:
            raise RuntimeError("No thermo_electric feature is available.")

        return float(thermo.read_temperature_degrees_celsius())

    def set_tec_target_c(self, temperature_c: float) -> None:
        thermo = self._thermo()

        if thermo is None:
            raise RuntimeError("No thermo_electric feature is available.")

        thermo.set_temperature_setpoint_degrees_celsius(float(temperature_c))

    def set_tec_enabled(self, enabled: bool) -> None:
        thermo = self._thermo()

        if thermo is None:
            raise RuntimeError("No thermo_electric feature is available.")

        # Some backends accept strings, some accept bools. Try the observed string form first.
        state = "on" if enabled else "off"

        try:
            thermo.enable_tec(state)
            return
        except TypeError:
            pass

        thermo.enable_tec(bool(enabled))


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
        print("QEPro hardware average method:", method)

        if method is None:
            return False

        method(int(max(1, averages)))
        return True

    def _integration_limits_us(self) -> tuple[int, int]:
        try:
            limits = getattr(self.spec, "integration_time_micros_limits", None)

            if callable(limits):
                min_us, max_us = limits()
            else:
                min_us, max_us = limits

            return int(min_us), int(max_us)

        except Exception:
            return 1, 60_000_000

    def _validate_integration_us(self, integration_us: int) -> int:
        integration_us = int(integration_us)

        min_us, max_us = self._integration_limits_us()

        if integration_us < min_us or integration_us > max_us:
            raise ValueError(
                f"Integration time {integration_us} us is outside spectrometer range "
                f"[{min_us}, {max_us}] us"
            )

        return integration_us

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

        integration_us = self._validate_integration_us(int(integration_ms * 1000))
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
