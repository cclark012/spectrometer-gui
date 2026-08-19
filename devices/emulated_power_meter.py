from __future__ import annotations

import math
import time

import numpy as np

from core.records import PowerSnapshot

VALID_CH1_STATUS = 0x118
VALID_CH2_STATUS = 0x108


class EmulatedPowerMeter:
    def __init__(self, *, random_seed: int | None = None) -> None:
        self._t0 = time.perf_counter()
        self._rng = np.random.default_rng(random_seed)
        self.wavelength_nm = 532
        self.min_wavelength_nm = 190
        self.max_wavelength_nm = 1100

    def read_all_power_with_status(self) -> PowerSnapshot:
        elapsed_s = time.perf_counter() - self._t0
        base = 7.0e-6
        drift = 1.0 + 0.0015 * math.sin(2.0 * math.pi * elapsed_s / 60.0)
        noise = 1.0 + self._rng.normal(0.0, 0.00025)

        ch1 = base * drift * noise
        ch2 = 0.095 * ch1 * (1.0 + self._rng.normal(0.0, 0.001))
        return PowerSnapshot(
            powers_w=[float(ch1), float(ch2)],
            pm_status=[VALID_CH1_STATUS, VALID_CH2_STATUS],
            command_status=0,
        )

    def set_wavelength_for_laser_nm(self, wavelength_nm: float) -> int:
        wavelength = int(round(float(wavelength_nm)))
        if not self.min_wavelength_nm <= wavelength <= self.max_wavelength_nm:
            raise ValueError(
                f"wavelength {wavelength} nm outside emulated range "
                f"[{self.min_wavelength_nm}, {self.max_wavelength_nm}] nm"
            )
        self.wavelength_nm = wavelength
        return 0

    def get_wavelength_nm(self) -> int:
        return int(self.wavelength_nm)

    def close(self) -> None:
        pass
