from __future__ import annotations

import math
import time

import numpy as np

from core.records import SpectralAcquisition, SpectrometerCapabilities


class EmulatedSpectrometer:
    """QEPro-like simulator used by the GUI's hardware-free mode."""

    def __init__(self, *, random_seed: int | None = None) -> None:
        self.name = "EmulatedSpectrometer"
        self.serial_number = "EMU-SPEC"
        self.max_intensity = 65535.0
        self.wavelengths_nm = np.linspace(250.0, 1050.0, 2048)

        self.integration_time_min_us = 1_000
        self.integration_time_max_us = 60_000_000
        self.tec_enabled = True
        self.tec_target_c = -10.0
        self.ccd_temperature_c = -10.0
        self.hardware_averages = 1

        self._t0 = time.perf_counter()
        self._rng = np.random.default_rng(random_seed)

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
        if mode not in {"software", "device"}:
            raise ValueError(
                f"Unknown averaging mode {averaging_mode!r}; expected 'software' or 'device'"
            )

        averages = max(1, int(averages))
        integration_us = self._validate_integration_us(int(integration_ms) * 1000)
        integration_ms = integration_us / 1000.0
        self.set_hardware_averages(averages if mode == "device" else 1)

        # Match the wall-clock behavior of N integrations without adding an
        # arbitrary cap that makes the emulator unrealistically fast.
        time.sleep(integration_ms * averages / 1000.0)

        running_mean: np.ndarray | None = None
        signal_max = float("-inf")
        for index in range(averages):
            values = self._single_spectrum(
                integration_ms=integration_ms,
                correct_dark=correct_dark,
                correct_nonlinearity=correct_nonlinearity,
            )
            signal_max = max(signal_max, float(np.nanmax(values)))
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
            device_averaging_used=(mode == "device"),
        )

    def _single_spectrum(
        self,
        *,
        integration_ms: float,
        correct_dark: bool,
        correct_nonlinearity: bool,
    ) -> np.ndarray:
        wavelengths = self.wavelengths_nm
        elapsed_s = time.perf_counter() - self._t0
        exposure_scale = float(integration_ms) / 100.0
        source_scale = 1.0 + 0.002 * math.sin(2.0 * math.pi * elapsed_s / 45.0)

        def peak(center: float, width: float, amplitude: float) -> np.ndarray:
            return amplitude * np.exp(-0.5 * ((wavelengths - center) / width) ** 2)

        dark = 60.0 + 5.0 * np.sin(wavelengths / 120.0)
        emission = (
            peak(470.0, 22.0, 4200.0)
            + peak(575.0, 28.0, 2900.0)
            + peak(655.0, 17.0, 1500.0)
            + peak(735.0, 42.0, 800.0)
            + peak(815.0, 28.0, 320.0)
        )

        values = dark + exposure_scale * source_scale * emission

        if correct_dark:
            values = values - dark
        if correct_nonlinearity:
            values = values / (1.0 + 1.0e-7 * values)

        shot_noise = self._rng.normal(
            0.0,
            0.15 * np.sqrt(np.maximum(values, 1.0)),
        )
        read_noise = self._rng.normal(0.0, 2.0, size=values.shape)
        noisy = values + shot_noise + read_noise

        # A corrected spectrum is allowed to cross zero.  Clipping it here
        # censors more than half of the pixels in a dark noise window and can
        # collapse robust noise estimators toward zero.  Raw/uncorrected counts
        # remain non-negative like detector ADC output.
        if correct_dark:
            return noisy
        return np.maximum(noisy, 0.0)

    def capabilities(self) -> SpectrometerCapabilities:
        return SpectrometerCapabilities(
            model=self.name,
            serial_number=self.serial_number,
            pixels=len(self.wavelengths_nm),
            max_intensity=self.max_intensity,
            integration_time_min_us=self.integration_time_min_us,
            integration_time_max_us=self.integration_time_max_us,
            features=["spectrometer", "emulator", "thermo_electric", "device_averaging"],
            feature_methods={
                "thermo_electric": [
                    "get_ccd_temperature_c",
                    "set_tec_target_c",
                    "set_tec_enabled",
                ],
                "device_averaging": ["set_hardware_averages"],
            },
            tec_supported=True,
            device_averaging_supported=True,
        )

    def get_ccd_temperature_c(self) -> float:
        return float(self.ccd_temperature_c)

    def set_tec_target_c(self, temperature_c: float) -> None:
        self.tec_target_c = float(temperature_c)
        if self.tec_enabled:
            self.ccd_temperature_c = self.tec_target_c

    def set_tec_enabled(self, enabled: bool) -> None:
        self.tec_enabled = bool(enabled)
        if self.tec_enabled:
            self.ccd_temperature_c = self.tec_target_c

    def set_hardware_averages(self, averages: int) -> bool:
        self.hardware_averages = max(1, int(averages))
        return True

    def _validate_integration_us(self, integration_us: int) -> int:
        value = int(integration_us)
        if not self.integration_time_min_us <= value <= self.integration_time_max_us:
            raise ValueError(
                f"Emulated integration time {value} us is outside range "
                f"[{self.integration_time_min_us}, {self.integration_time_max_us}] us"
            )
        return value

    def close(self) -> None:
        pass
