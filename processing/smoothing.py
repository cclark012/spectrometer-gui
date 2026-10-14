from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

SMOOTHING_WINDOWS = (
    "none",
    "boxcar",
    "hann",
    "hamming",
    "gaussian",
    "triangle",
    "blackman",
    "cosine",
    "sine",
)
SMOOTHING_BOUNDARIES = ("nearest", "reflect", "wrap", "constant")


def smoothing_kernel(window: str, width: int) -> NDArray[np.float64]:
    """Return normalized smoothing weights for a named window.

    ``width`` is the number of samples for every window except Gaussian,
    where it is the full width at half maximum (FWHM) in samples. The Gaussian
    support extends to roughly four standard deviations on either side.
    """

    name = str(window).strip().lower()
    width = int(width)
    if name not in SMOOTHING_WINDOWS:
        raise ValueError(
            f"Unknown smoothing window {window!r}; expected one of "
            f"{', '.join(SMOOTHING_WINDOWS)}."
        )
    if name == "none" or width <= 1:
        return np.ones(1, dtype=float)
    if width < 1:
        raise ValueError("Smoothing width must be positive.")

    if name == "boxcar":
        weights = np.ones(width, dtype=float)
    elif name == "hann":
        weights = np.hanning(width)
    elif name == "hamming":
        weights = np.hamming(width)
    elif name == "blackman":
        weights = np.blackman(width)
    elif name == "triangle":
        # Bartlett is zero at both ends. This centered triangle retains useful
        # non-zero edge weights for the smallest selectable widths.
        positions = np.arange(width, dtype=float)
        weights = 1.0 - np.abs((positions - (width - 1) / 2.0) / ((width + 1) / 2.0))
    elif name in {"cosine", "sine"}:
        positions = np.arange(width, dtype=float)
        if name == "cosine":
            weights = np.cos(
                np.pi * (positions - (width - 1) / 2.0) / width
            )
        else:
            # Interior-sample sine window. This differs slightly from the
            # half-sample-shifted cosine window at short widths while staying
            # well-defined for a two-sample window.
            weights = np.sin(np.pi * (positions + 1.0) / (width + 1.0))
    else:  # gaussian
        sigma = float(width) / (2.0 * np.sqrt(2.0 * np.log(2.0)))
        radius = max(1, int(np.ceil(4.0 * sigma)))
        positions = np.arange(-radius, radius + 1, dtype=float)
        weights = np.exp(-0.5 * np.square(positions / sigma))

    total = float(np.sum(weights))
    if not np.isfinite(total) or total <= 0.0:
        raise ValueError(
            f"The {name} window is undefined for width {width}; choose a larger width."
        )
    return np.asarray(weights / total, dtype=float)


def smooth_spectrum(
    values: NDArray[np.float64],
    *,
    window: str,
    width: int,
    boundary: str = "nearest",
) -> NDArray[np.float64]:
    """Smooth a one-dimensional spectrum while preserving its sample count."""

    array = np.asarray(values, dtype=float)
    if array.ndim != 1:
        raise ValueError("A spectrum must be one-dimensional for smoothing.")
    kernel = smoothing_kernel(window, width)
    if kernel.size == 1:
        return array
    mode = str(boundary).strip().lower()
    if mode not in SMOOTHING_BOUNDARIES:
        raise ValueError(
            f"Unknown smoothing boundary {boundary!r}; expected one of "
            f"{', '.join(SMOOTHING_BOUNDARIES)}."
        )
    left = (kernel.size - 1) // 2
    right = kernel.size - 1 - left
    pad_mode = {
        "nearest": "edge",
        "reflect": "reflect",
        "wrap": "wrap",
        "constant": "constant",
    }[mode]
    padded = np.pad(array, (left, right), mode=pad_mode)
    return np.convolve(padded, kernel, mode="valid")


def boxcar_smooth(values: NDArray[np.float64], width: int) -> NDArray[np.float64]:
    """Legacy zero-padded boxcar helper retained for compatibility."""
    width = int(width)
    array = np.asarray(values, dtype=float)

    if width <= 1:
        return array
    if width > array.size:
        raise ValueError(
            f"Boxcar width {width} exceeds spectrum length {array.size}."
        )

    kernel = np.full(width, 1.0 / width, dtype=float)
    return np.convolve(array, kernel, mode="same")
