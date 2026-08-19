from __future__ import annotations

import math


def wavelength_to_rgb(wavelength_nm: float) -> tuple[int, int, int]:
    """Approximate a visible wavelength as sRGB for a subtle UI cue."""

    wavelength = float(wavelength_nm)
    if not math.isfinite(wavelength) or wavelength < 380.0:
        return 210, 210, 210
    elif wavelength > 780.0:
        return 55, 55, 55

    if wavelength <= 440.0:
        red, green, blue = -(wavelength - 440.0) / 60.0, 0.0, 1.0
    elif wavelength <= 490.0:
        red, green, blue = 0.0, (wavelength - 440.0) / 50.0, 1.0
    elif wavelength <= 510.0:
        red, green, blue = 0.0, 1.0, -(wavelength - 510.0) / 20.0
    elif wavelength <= 580.0:
        red, green, blue = (wavelength - 510.0) / 70.0, 1.0, 0.0
    elif wavelength <= 645.0:
        red, green, blue = 1.0, -(wavelength - 645.0) / 65.0, 0.0
    else:
        red, green, blue = 1.0, 0.0, 0.0

    if wavelength <= 420.0:
        attenuation = 0.3 + 0.7 * (wavelength - 380.0) / 40.0
    elif wavelength <= 700.0:
        attenuation = 1.0
    else:
        attenuation = 0.3 + 0.7 * (780.0 - wavelength) / 80.0

    def to_srgb(channel: float) -> int:
        linear = max(0.0, min(1.0, channel * attenuation))
        if linear <= 0.0031308:
            encoded = 12.92 * linear
        else:
            encoded = 1.055 * linear ** (1.0 / 2.4) - 0.055
        return int(round(255.0 * max(0.0, min(1.0, encoded))))

    return to_srgb(red), to_srgb(green), to_srgb(blue)
