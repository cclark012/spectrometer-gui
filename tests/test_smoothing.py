from __future__ import annotations

import numpy as np

from processing.smoothing import SMOOTHING_WINDOWS, smooth_spectrum, smoothing_kernel


def test_every_smoothing_kernel_is_normalized_and_symmetric() -> None:
    for name in SMOOTHING_WINDOWS:
        kernel = smoothing_kernel(name, 9)
        assert np.isclose(np.sum(kernel), 1.0)
        np.testing.assert_allclose(kernel, kernel[::-1])


def test_nearest_boundary_preserves_constant_signal() -> None:
    values = np.full(25, 7.5)
    for name in SMOOTHING_WINDOWS:
        smoothed = smooth_spectrum(
            values,
            window=name,
            width=7,
            boundary="nearest",
        )
        np.testing.assert_allclose(smoothed, values)


def test_gaussian_width_is_fwhm_and_output_shape_is_preserved() -> None:
    kernel = smoothing_kernel("gaussian", 8)
    center = kernel.size // 2
    nearest_half = int(np.argmin(np.abs(kernel[center:] - kernel[center] / 2.0)))
    assert abs(2 * nearest_half - 8) <= 1
    values = np.arange(32, dtype=float)
    assert smooth_spectrum(
        values,
        window="gaussian",
        width=8,
        boundary="reflect",
    ).shape == values.shape


def test_unknown_window_or_boundary_is_rejected() -> None:
    values = np.arange(5, dtype=float)
    for kwargs in (
        {"window": "mystery", "width": 3, "boundary": "nearest"},
        {"window": "boxcar", "width": 3, "boundary": "mystery"},
    ):
        try:
            smooth_spectrum(values, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("Unknown smoothing options must be rejected.")
