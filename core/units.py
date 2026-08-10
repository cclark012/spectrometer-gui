from __future__ import annotations

import math
import re

_INVALID_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._+\-]+")


def sanitize_component(text: str) -> str:
    text = text.strip()
    text = _INVALID_FILENAME_CHARS.sub("_", text)
    text = text.strip("._ ")

    return text or "untitled"


def compact_float_token(value: float, precision: int = 6) -> str:
    if not math.isfinite(value):
        return "nan"

    text = f"{value:.{precision}g}"
    text = text.replace("+", "")
    text = text.replace(".", "-")

    return text


def power_token(power_w: float) -> str:
    if not math.isfinite(power_w):
        return "nan_W"

    abs_p = abs(power_w)

    if abs_p >= 1.0:
        value = power_w
        unit = "W"
    elif abs_p >= 1e-3:
        value = power_w * 1e3
        unit = "mW"
    elif abs_p >= 1e-6:
        value = power_w * 1e6
        unit = "uW"
    elif abs_p >= 1e-9:
        value = power_w * 1e9
        unit = "nW"
    else:
        value = power_w
        unit = "W"

    return f"{compact_float_token(value)}{unit}"


def format_power_w(power_w: float, significant_digits: int = 6) -> str:
    """Format a power value stored in watts using an appropriate SI unit."""

    power = float(power_w)
    digits = max(1, int(significant_digits))
    if not math.isfinite(power):
        return "--"

    absolute = abs(power)
    if absolute >= 1.0:
        return f"{power:.{digits}g} W"
    if absolute >= 1e-3:
        return f"{power * 1e3:.{digits}g} mW"
    if absolute >= 1e-6:
        return f"{power * 1e6:.{digits}g} μW"
    if absolute >= 1e-9:
        return f"{power * 1e9:.{digits}g} nW"
    return f"{power:.{digits}e} W"


def field_token(field_mT: float) -> str:
    if not math.isfinite(field_mT):
        return "nan_mT"

    return f"{compact_float_token(field_mT)}mT"
