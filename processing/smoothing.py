from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def boxcar_smooth(values: NDArray[np.float64], width: int) -> NDArray[np.float64]:
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
