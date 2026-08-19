from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from core.records import BackgroundSpectrum


class BackgroundCorrector:
    """Store a background spectrum and cache it on the active wavelength grid."""

    def __init__(self) -> None:
        self.background: BackgroundSpectrum | None = None
        self._cached_grid: NDArray[np.float64] | None = None
        self._counts_per_s_on_grid: NDArray[np.float64] | None = None

    def set_background(self, background: BackgroundSpectrum) -> None:
        self._validate_background(background)
        self.background = background
        self._clear_cache()

    def clear(self) -> None:
        self.background = None
        self._clear_cache()

    def _clear_cache(self) -> None:
        self._cached_grid = None
        self._counts_per_s_on_grid = None

    @staticmethod
    def _validate_background(background: BackgroundSpectrum) -> None:
        wavelengths = np.asarray(background.wavelengths_nm, dtype=float)
        counts_per_s = np.asarray(background.counts_per_s, dtype=float)

        if wavelengths.ndim != 1 or counts_per_s.ndim != 1:
            raise ValueError("background wavelength and count arrays must be one-dimensional")
        if wavelengths.shape != counts_per_s.shape:
            raise ValueError("background wavelength and count arrays must have equal shapes")
        if np.count_nonzero(np.isfinite(wavelengths) & np.isfinite(counts_per_s)) < 2:
            raise ValueError("background spectrum requires at least two finite points")
        if int(background.integration_ms) <= 0:
            raise ValueError("background integration time must be positive")

    @staticmethod
    def _prepare_background_arrays(
        background: BackgroundSpectrum,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        wavelengths = np.asarray(background.wavelengths_nm, dtype=float)
        counts_per_s = np.asarray(background.counts_per_s, dtype=float)
        finite = np.isfinite(wavelengths) & np.isfinite(counts_per_s)
        wavelengths = wavelengths[finite]
        counts_per_s = counts_per_s[finite]

        order = np.argsort(wavelengths, kind="stable")
        wavelengths = wavelengths[order]
        counts_per_s = counts_per_s[order]

        # Duplicate wavelength entries are legal in raw data but ambiguous for
        # interpolation. Collapse them by averaging their response values.
        unique_wavelengths, inverse = np.unique(wavelengths, return_inverse=True)
        if unique_wavelengths.size != wavelengths.size:
            sums = np.zeros(unique_wavelengths.size, dtype=float)
            counts = np.zeros(unique_wavelengths.size, dtype=int)
            np.add.at(sums, inverse, counts_per_s)
            np.add.at(counts, inverse, 1)
            counts_per_s = sums / counts
            wavelengths = unique_wavelengths

        if wavelengths.size < 2:
            raise ValueError("background spectrum requires at least two unique wavelengths")

        return wavelengths, counts_per_s

    def _background_on_grid(
        self,
        wavelengths_nm: NDArray[np.float64],
    ) -> NDArray[np.float64]:
        if self.background is None:
            raise RuntimeError("No background spectrum is available.")

        current = np.asarray(wavelengths_nm, dtype=float)
        if current.ndim != 1:
            raise ValueError("wavelength grid must be one-dimensional")
        if not np.all(np.isfinite(current)):
            raise ValueError("wavelength grid contains non-finite values")

        if (
            self._cached_grid is not None
            and self._counts_per_s_on_grid is not None
            and np.array_equal(current, self._cached_grid)
        ):
            return self._counts_per_s_on_grid

        raw_bg_wavelengths = np.asarray(self.background.wavelengths_nm, dtype=float)
        raw_bg_counts = np.asarray(self.background.counts_per_s, dtype=float)

        if (
            current.shape == raw_bg_wavelengths.shape
            and np.all(np.isfinite(raw_bg_wavelengths))
            and np.all(np.isfinite(raw_bg_counts))
            and np.allclose(current, raw_bg_wavelengths, rtol=0.0, atol=1e-9)
        ):
            result = raw_bg_counts.copy()
        else:
            bg_wavelengths, bg_counts_per_s = self._prepare_background_arrays(
                self.background
            )
            result = np.interp(current, bg_wavelengths, bg_counts_per_s)

        self._cached_grid = current.copy()
        self._counts_per_s_on_grid = np.asarray(result, dtype=float)
        return self._counts_per_s_on_grid

    def apply(
        self,
        *,
        wavelengths_nm: NDArray[np.float64],
        intensities_counts: NDArray[np.float64],
        integration_ms: int,
    ) -> tuple[NDArray[np.float64], bool, str, int]:
        wavelengths = np.asarray(wavelengths_nm, dtype=float)
        intensities = np.asarray(intensities_counts, dtype=float)

        if wavelengths.ndim != 1 or intensities.ndim != 1:
            raise ValueError("wavelengths and intensities must be one-dimensional")
        if wavelengths.shape != intensities.shape:
            raise ValueError("wavelengths and intensities must have equal shapes")
        if int(integration_ms) <= 0:
            raise ValueError("integration time must be positive")

        if self.background is None:
            return intensities.copy(), False, "", 0

        background_counts_per_s = self._background_on_grid(wavelengths)
        corrected = intensities - background_counts_per_s * (float(integration_ms) * 1.0e-3)

        return (
            corrected,
            True,
            self.background.timestamp_utc,
            int(self.background.integration_ms),
        )
