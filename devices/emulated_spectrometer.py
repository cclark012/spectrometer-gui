from __future__ import annotations

import math
import time
import numpy as np

from core.records import SpectrometerCapabilities

class EmulatedSpectrometer:
    def __init__(self) -> None:
        self.name = "EmulatedSpectrometer"
        self.serial_number = "EMU-SPEC"
        self.max_intensity = 65535.0

        self.wavelengths_nm = np.linspace(250.0, 1050.0, 2048)
        self.t0 = time.perf_counter()
        self.rng = np.random.default_rng()
        
        self.integration_time_min_us = 1000
        self.integration_time_max_us = 60_000_000

    def acquire_spectrum(
        self,
        *,
        integration_ms: int,
        averages: int,
        correct_dark: bool,
        correct_nonlinearity: bool,
        averaging_mode: str = "software",
    ) -> tuple[np.ndarray, np.ndarray, float]:
        averages = max(1, int(averages))
        integration_ms = max(1, int(integration_ms))

        time.sleep(integration_ms * averages / 1000.0)

        traces = []
        signal_max_counts = float("-inf")

        for _ in range(averages):
            y = self._single_spectrum(
                integration_ms=integration_ms,
                correct_dark=correct_dark,
                correct_nonlinearity=correct_nonlinearity,
            )

            signal_max_counts = max(signal_max_counts, float(np.nanmax(y)))
            traces.append(y)

        return (
            self.wavelengths_nm.copy(),
            np.mean(np.vstack(traces), axis=0),
            signal_max_counts,
        )

    def _single_spectrum(
        self,
        *,
        integration_ms: int,
        correct_dark: bool,
        correct_nonlinearity: bool,
    ) -> np.ndarray:
        wl = self.wavelengths_nm
        t = time.perf_counter() - self.t0

        integration_us = self._validate_integration_us(int(integration_ms * 1000))
        integration_ms = integration_us / 1000.0
        exposure_scale = integration_ms / 100.0
        source_scale = 1.0 + 0.002 * math.sin(2.0 * math.pi * t / 45.0)

        def peak(center: float, width: float, amplitude: float) -> np.ndarray:
            return amplitude * np.exp(-0.5 * ((wl - center) / width) ** 2)

        dark = 60.0 + 5.0 * np.sin(wl / 120.0)

        spectrum = (
            peak(470.0, 22.0, 4200.0)
            + peak(575.0, 28.0, 2900.0)
            + peak(655.0, 17.0, 1500.0)
            + peak(735.0, 42.0, 800.0)
            + peak(815.0, 28.0, 320.0)
        )

        y = dark + exposure_scale * source_scale * spectrum

        if correct_dark:
            y = y - dark

        if correct_nonlinearity:
            # Mild synthetic correction. Real correction is handled by seabreeze.
            y = y / (1.0 + 1.0e-7 * y)

        shot_like_noise = self.rng.normal(0.0, 0.15 * np.sqrt(np.maximum(y, 1.0)))
        read_noise = self.rng.normal(0.0, 2.0, size=y.shape)

        return np.maximum(y + shot_like_noise + read_noise, 0.0)

    def capabilities(self) -> SpectrometerCapabilities:
        return SpectrometerCapabilities(
            model="EmulatedSpectrometer",
            serial_number="EMU-SPEC",
            pixels=len(self.wavelengths_nm),
            max_intensity=float(self.max_intensity),
            integration_time_min_us=1000,
            integration_time_max_us=60_000_000,
            features=["spectrometer", "emulator", "tec", "device_averaging"],
            feature_methods={
                "tec": [
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
        return float(getattr(self, "ccd_temperature_c", -10.0))

    def set_tec_target_c(self, temperature_c: float) -> None:
        self.ccd_temperature_c = float(temperature_c)

    def set_tec_enabled(self, enabled: bool) -> None:
        self.tec_enabled = bool(enabled)

    def set_hardware_averages(self, averages: int) -> bool:
        self.hardware_averages = int(max(1, averages))
        return True

    def _validate_integration_us(self, integration_us: int) -> int:
        integration_us = int(integration_us)

        if integration_us < self.integration_time_min_us or integration_us > self.integration_time_max_us:
            raise ValueError(
                f"Emulated integration time {integration_us} us is outside range "
                f"[{self.integration_time_min_us}, {self.integration_time_max_us}] us"
            )

        return integration_us

    def close(self) -> None:
        pass
