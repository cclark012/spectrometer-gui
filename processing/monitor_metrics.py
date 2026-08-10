from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from core.records import MonitorTracePoint, SpectrumRecord


@dataclass(frozen=True, slots=True)
class MonitorCaptureConfig:
    target_wavelength_nm: float
    integration_start_nm: float
    integration_stop_nm: float
    application_t0_s: float


def _sorted_finite_spectrum(
    wavelengths_nm: np.ndarray,
    intensities_counts: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    wavelengths = np.asarray(wavelengths_nm, dtype=float)
    intensities = np.asarray(intensities_counts, dtype=float)

    if wavelengths.ndim != 1 or intensities.ndim != 1:
        raise ValueError("wavelengths and intensities must be one-dimensional")
    if wavelengths.shape != intensities.shape:
        raise ValueError("wavelengths and intensities must have equal shapes")

    finite = np.isfinite(wavelengths) & np.isfinite(intensities)
    wavelengths = wavelengths[finite]
    intensities = intensities[finite]

    if wavelengths.size:
        order = np.argsort(wavelengths, kind="stable")
        wavelengths = wavelengths[order]
        intensities = intensities[order]

    return wavelengths, intensities


def build_monitor_point(
    record: SpectrumRecord,
    config: MonitorCaptureConfig,
) -> MonitorTracePoint:
    wavelengths, intensities = _sorted_finite_spectrum(
        record.wavelengths_nm,
        record.intensities_counts,
    )
    target_nm = float(config.target_wavelength_nm)
    start_nm = float(min(config.integration_start_nm, config.integration_stop_nm))
    stop_nm = float(max(config.integration_start_nm, config.integration_stop_nm))

    if (
        wavelengths.size >= 2
        and wavelengths[0] <= target_nm <= wavelengths[-1]
    ):
        target_intensity = float(np.interp(target_nm, wavelengths, intensities))
    else:
        target_intensity = float("nan")

    range_mask = (wavelengths >= start_nm) & (wavelengths <= stop_nm)
    integrated_range = (
        float(np.trapezoid(intensities[range_mask], wavelengths[range_mask]))
        if np.count_nonzero(range_mask) >= 2
        else float("nan")
    )
    total_integrated = (
        float(np.trapezoid(intensities, wavelengths))
        if wavelengths.size >= 2
        else float("nan")
    )

    if intensities.size:
        peak_index = int(np.argmax(intensities))
        peak_intensity = float(intensities[peak_index])
        peak_wavelength = float(wavelengths[peak_index])
        signal_mean = float(np.mean(intensities))
        signal_max = float(np.max(intensities))
    else:
        peak_intensity = peak_wavelength = signal_mean = signal_max = float("nan")

    if math.isfinite(float(record.signal_max_counts)):
        signal_max = float(record.signal_max_counts)

    return MonitorTracePoint(
        timestamp_utc=record.timestamp_utc,
        elapsed_s=float(record.timestamp_s - config.application_t0_s),
        field_mT=float(record.field_value),
        power_ch1_W=float(record.mean_power_w(0)),
        power_ch2_W=float(record.mean_power_w(1)),
        intensity_target_counts=target_intensity,
        intensity_target_nm=target_nm,
        integrated_range_counts_nm=integrated_range,
        integration_start_nm=start_nm,
        integration_stop_nm=stop_nm,
        total_integrated_counts_nm=total_integrated,
        peak_intensity_counts=peak_intensity,
        peak_wavelength_nm=peak_wavelength,
        signal_max_counts=signal_max,
        signal_mean_counts=signal_mean,
    )


def monitor_x(point: MonitorTracePoint, mode: str) -> tuple[float, str, str]:
    if mode == "Power ch1":
        return point.power_ch1_W, "Power ch1", "W"
    if mode == "Magnetic field":
        return point.field_mT, "Magnetic field", "mT"
    return point.elapsed_s, "Time", "s"


def safe_divide(numerator: float, denominator: float) -> float:
    numerator = float(numerator)
    denominator = float(denominator)
    if not math.isfinite(numerator):
        return float("nan")
    if not math.isfinite(denominator) or denominator == 0.0:
        return float("nan")
    return numerator / denominator


def monitor_y(point: MonitorTracePoint, mode: str) -> tuple[float, str, str]:
    values: dict[str, tuple[float, str, str]] = {
        "Intensity at captured wavelength": (
            point.intensity_target_counts,
            "Intensity at captured wavelength",
            "counts",
        ),
        "Integrated captured range": (
            point.integrated_range_counts_nm,
            "Integrated captured range",
            "counts nm",
        ),
        "Total integrated intensity": (
            point.total_integrated_counts_nm,
            "Total integrated intensity",
            "counts nm",
        ),
        "Intensity at captured wavelength / power ch1": (
            safe_divide(point.intensity_target_counts, point.power_ch1_W),
            "Intensity at captured wavelength / power ch1",
            "counts/W",
        ),
        "Integrated captured range / power ch1": (
            safe_divide(point.integrated_range_counts_nm, point.power_ch1_W),
            "Integrated captured range / power ch1",
            "counts nm/W",
        ),
        "Total integrated intensity / power ch1": (
            safe_divide(point.total_integrated_counts_nm, point.power_ch1_W),
            "Total integrated intensity / power ch1",
            "counts nm/W",
        ),
        "Peak intensity": (point.peak_intensity_counts, "Peak intensity", "counts"),
        "Peak wavelength": (point.peak_wavelength_nm, "Peak wavelength", "nm"),
        "Signal max": (point.signal_max_counts, "Signal max", "counts"),
        "Signal mean": (point.signal_mean_counts, "Signal mean", "counts"),
        "Power ch1": (point.power_ch1_W, "Power ch1", "W"),
    }
    return values.get(mode, (float("nan"), "Tracked quantity", ""))
