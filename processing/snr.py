from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
from numpy.typing import NDArray

from core.snr_records import AcquisitionSuggestion, SNRMetrics

FloatArray = NDArray[np.float64]

def _prepare_xy(
    wavelengths_nm: Iterable[float],
    intensities_counts: Iterable[float],
) -> tuple[FloatArray, FloatArray]:
    x = np.asarray(wavelengths_nm, dtype=float)
    y = np.asarray(intensities_counts, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or x.shape != y.shape:
        raise ValueError("wavelength and intensity arrays must be equal-length 1D arrays")
    finite = np.isfinite(x) & np.isfinite(y)
    x = x[finite]
    y = y[finite]
    if x.size < 4:
        raise ValueError("too few finite spectral samples")
    order = np.argsort(x, kind="stable")
    x = x[order]
    y = y[order]
    # Collapse duplicate wavelength entries to avoid zero-width trapezoid segments.
    unique_x, inverse = np.unique(x, return_inverse=True)
    if unique_x.size != x.size:
        sums = np.bincount(inverse, weights=y)
        counts = np.bincount(inverse)
        y = sums / np.maximum(counts, 1)
        x = unique_x
    return x.astype(float, copy=False), y.astype(float, copy=False)


def _interval_mask(x: FloatArray, start: float, stop: float) -> NDArray[np.bool_]:
    lo, hi = sorted((float(start), float(stop)))
    return (x >= lo) & (x <= hi)


def _robust_sigma(values: FloatArray) -> float:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return float("nan")
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    sigma = 1.4826 * mad
    if not math.isfinite(sigma) or sigma <= 0:
        sigma = float(np.std(values, ddof=1))
    return sigma


def _fit_baseline(
    x: FloatArray,
    y: FloatArray,
    order: int,
    *,
    sigma_clip: float = 4.5,
    iterations: int = 3,
) -> tuple[FloatArray, FloatArray]:
    order = max(0, min(int(order), 2))
    if x.size < order + 2:
        raise ValueError("too few noise pixels for the requested baseline order")
    center = float(np.mean(x))
    scale = float(np.ptp(x))
    if scale <= 0:
        scale = 1.0
    z = (x - center) / scale
    keep = np.ones(x.size, dtype=bool)
    coefficients: FloatArray | None = None
    for _ in range(max(1, int(iterations))):
        if np.count_nonzero(keep) < order + 2:
            break
        coefficients = np.polynomial.polynomial.polyfit(z[keep], y[keep], order)
        fitted = np.polynomial.polynomial.polyval(z, coefficients)
        residual = y - fitted
        sigma = _robust_sigma(residual[keep])
        if not math.isfinite(sigma) or sigma <= 0:
            break
        median = float(np.median(residual[keep]))
        new_keep = np.abs(residual - median) <= sigma_clip * sigma
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep
    if coefficients is None:
        raise ValueError("baseline fit failed")
    return coefficients, np.asarray([center, scale], dtype=float)


def _evaluate_baseline(
    x: FloatArray,
    coefficients: FloatArray,
    transform: FloatArray,
) -> FloatArray:
    center, scale = float(transform[0]), float(transform[1])
    z = (x - center) / scale
    return np.polynomial.polynomial.polyval(z, coefficients)


def _trapezoid_weights(x: FloatArray) -> FloatArray:
    if x.size < 2:
        return np.zeros_like(x)
    dx = np.diff(x)
    if np.any(dx <= 0):
        raise ValueError("wavelengths must be strictly increasing")
    weights = np.empty_like(x)
    weights[0] = dx[0] / 2.0
    weights[-1] = dx[-1] / 2.0
    if x.size > 2:
        weights[1:-1] = (dx[:-1] + dx[1:]) / 2.0
    return weights


def estimate_snr(
    wavelengths_nm: Iterable[float],
    intensities_counts: Iterable[float],
    *,
    signal_start_nm: float,
    signal_stop_nm: float,
    noise_intervals_nm: Iterable[tuple[float, float]],
    baseline_order: int = 1,
    minimum_noise_pixels: int = 20,
    peak_percentile: float = 99.5,
    full_scale_counts: float = float("nan"),
) -> SNRMetrics:
    """Estimate peak and integrated-band SNR from an unsmoothed spectrum.

    Noise is estimated from one or more user-selected noise windows after a robust
    polynomial baseline fit. The integrated-noise estimate assumes independent
    per-pixel noise; repeat-based stability measurements remain the better measure
    when drift or correlated noise dominates.
    """

    try:
        x, y = _prepare_xy(wavelengths_nm, intensities_counts)
        signal_mask = _interval_mask(x, signal_start_nm, signal_stop_nm)
        noise_mask = np.zeros(x.size, dtype=bool)
        intervals = list(noise_intervals_nm)
        if not intervals:
            return SNRMetrics.invalid("no noise interval configured")
        for start, stop in intervals:
            noise_mask |= _interval_mask(x, start, stop)
        # Noise pixels should not include the designated signal band.
        noise_mask &= ~signal_mask
        n_signal = int(np.count_nonzero(signal_mask))
        n_noise = int(np.count_nonzero(noise_mask))
        if n_signal < 2:
            return SNRMetrics.invalid("signal interval contains fewer than two pixels")
        if n_noise < max(4, int(minimum_noise_pixels)):
            return SNRMetrics.invalid(
                f"noise intervals contain only {n_noise} pixels; "
                f"need at least {minimum_noise_pixels}"
            )

        coefficients, transform = _fit_baseline(
            x[noise_mask],
            y[noise_mask],
            baseline_order,
        )
        baseline_all = _evaluate_baseline(x, coefficients, transform)
        noise_residual = y[noise_mask] - baseline_all[noise_mask]
        sigma = _robust_sigma(noise_residual)
        if not math.isfinite(sigma) or sigma <= 0:
            return SNRMetrics.invalid("noise estimate is zero or non-finite")

        signal_x = x[signal_mask]
        corrected = y[signal_mask] - baseline_all[signal_mask]
        percentile = min(100.0, max(50.0, float(peak_percentile)))
        peak_signal = float(np.percentile(corrected, percentile))
        mean_signal = float(np.mean(corrected))
        weights = _trapezoid_weights(signal_x)
        integrated_signal = float(np.sum(weights * corrected))
        integrated_noise = float(sigma * np.sqrt(np.sum(weights**2)))
        peak_snr = peak_signal / sigma
        integrated_snr = (
            integrated_signal / integrated_noise
            if integrated_noise > 0
            else float("nan")
        )
        center_nm = 0.5 * (float(signal_x[0]) + float(signal_x[-1]))
        baseline_center = float(
            _evaluate_baseline(
                np.asarray([center_nm], dtype=float),
                coefficients,
                transform,
            )[0]
        )
        raw_peak = float(np.nanmax(y[signal_mask]))
        peak_fraction = (
            raw_peak / float(full_scale_counts)
            if math.isfinite(full_scale_counts) and full_scale_counts > 0
            else float("nan")
        )
        return SNRMetrics(
            valid=True,
            message="ok",
            peak_snr=float(peak_snr),
            integrated_snr=float(integrated_snr),
            noise_sigma_counts=float(sigma),
            peak_signal_counts=float(peak_signal),
            integrated_signal_counts_nm=float(integrated_signal),
            integrated_noise_counts_nm=float(integrated_noise),
            mean_signal_counts=float(mean_signal),
            baseline_at_signal_center_counts=float(baseline_center),
            peak_fraction_of_full_scale=float(peak_fraction),
            n_signal_pixels=n_signal,
            n_noise_pixels=n_noise,
        )
    except Exception as exc:
        return SNRMetrics.invalid(str(exc))


def selected_snr(
    result: SNRMetrics,
    metric: str,
) -> float:
    name = str(metric).strip().lower()

    if name == "peak":
        return float(result.peak_snr)

    if name == "integrated":
        return float(result.integrated_snr)

    raise ValueError(
        f"Unknown SNR metric: {metric!r}"
    )


def suggest_acquisition(
    *,
    result: SNRMetrics,
    metric: str,
    current_integration_ms: int,
    current_averages: int,
    target_snr: float,
    target_peak_fraction: float,
    minimum_integration_ms: int,
    maximum_integration_ms: int,
    maximum_averages: int,
    maximum_total_acquisition_s: float,
) -> AcquisitionSuggestion:
    """Suggest bounded integration/averaging settings from one SNR estimate.

    The prediction uses square-root exposure scaling and should be verified with a
    subsequent acquisition. It is not a replacement for a closed-loop controller.
    """

    integration = max(1, int(current_integration_ms))
    averages = max(1, int(current_averages))
    min_integration = max(1, int(minimum_integration_ms))
    max_integration = max(min_integration, int(maximum_integration_ms))
    max_averages = max(1, int(maximum_averages))
    max_total_s = max(0.001, float(maximum_total_acquisition_s))
    if not result.valid:
        return AcquisitionSuggestion(
            integration_ms=integration,
            averages=averages,
            predicted_snr=float("nan"),
            predicted_peak_fraction=float("nan"),
            changed=False,
            limiting_reason=f"invalid SNR estimate: {result.message}",
        )

    current_snr = selected_snr(result, metric)
    peak_fraction = float(result.peak_fraction_of_full_scale)
    target_fraction = min(0.90, max(0.10, float(target_peak_fraction)))
    target_snr = max(0.1, float(target_snr))

    new_integration = integration
    reason_parts: list[str] = []
    if math.isfinite(peak_fraction) and peak_fraction > 0:
        scale = target_fraction / peak_fraction
        # Limit a single suggestion to a 10x change; a later acquisition verifies it.
        scale = min(10.0, max(0.10, scale))
        new_integration = int(round(integration * scale))
        new_integration = min(max(new_integration, min_integration), max_integration)
    else:
        reason_parts.append("full-scale fraction unavailable")

    exposure_gain = max(new_integration / integration, 1e-12)
    predicted_after_integration = (
        current_snr * math.sqrt(exposure_gain)
        if math.isfinite(current_snr) and current_snr > 0
        else float("nan")
    )
    new_averages = averages
    if (
        math.isfinite(predicted_after_integration)
        and predicted_after_integration > 0
        and predicted_after_integration < target_snr
    ):
        required = averages * (target_snr / predicted_after_integration) ** 2
        new_averages = min(max_averages, max(1, int(math.ceil(required))))

    max_by_time = max(1, int(math.floor(1000.0 * max_total_s / new_integration)))
    if new_averages > max_by_time:
        new_averages = max_by_time
        reason_parts.append("limited by maximum total acquisition time")
    if new_averages >= max_averages:
        reason_parts.append("limited by maximum averages")
    if new_integration >= max_integration:
        reason_parts.append("limited by maximum integration time")

    total_gain = (new_integration * new_averages) / (integration * averages)
    predicted_snr = (
        current_snr * math.sqrt(max(total_gain, 0.0))
        if math.isfinite(current_snr)
        else float("nan")
    )
    predicted_fraction = (
        peak_fraction * new_integration / integration
        if math.isfinite(peak_fraction)
        else float("nan")
    )
    return AcquisitionSuggestion(
        integration_ms=int(new_integration),
        averages=int(new_averages),
        predicted_snr=float(predicted_snr),
        predicted_peak_fraction=float(predicted_fraction),
        changed=(new_integration != integration or new_averages != averages),
        limiting_reason="; ".join(reason_parts) if reason_parts else "target-based suggestion",
    )
