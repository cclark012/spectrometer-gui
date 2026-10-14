"""Fast single-frame SNR estimates, independent of user wavelength windows.

Noise is estimated on unsmoothed data. These are spatial noise estimates, not
repeat-acquisition uncertainty estimates; correlated noise and dense narrow
features can bias them. Local linear detrending removes slopes within blocks.
"""
import math
import numpy as np
from core.snr_records import SNRMetrics
from processing.snr import _prepare_xy


def block_noise(x, y, block_size=16, method='std'):
    if block_size not in (8, 16):
        raise ValueError('Block size must be 8 or 16')
    if method not in ('std', 'mad'):
        raise ValueError('Noise method must be std or mad')
    n = len(y) // block_size
    if n < 4:
        raise ValueError('Automatic SNR needs at least four complete blocks')
    xb = np.asarray(x[:n * block_size]).reshape(n, block_size)
    yb = np.asarray(y[:n * block_size]).reshape(n, block_size)
    xc = xb - xb.mean(axis=1, keepdims=True)
    yc = yb - yb.mean(axis=1, keepdims=True)
    residual = yc - xc * ((xc * yc).sum(axis=1) / (xc * xc).sum(axis=1))[:, None]
    if method == 'std':
        sigmas = np.sqrt((residual * residual).sum(axis=1) / (block_size - 2))
        # Unbias Gaussian standard deviations (rather than variances).
        df = block_size - 2
        sigmas /= math.sqrt(2 / df) * math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2))
    else:
        sigmas = (1.4826 * np.median(np.abs(residual - np.median(residual, axis=1)[:, None]), axis=1)
                  * math.sqrt(block_size / (block_size - 2)))
    # Symmetric trimmed mean avoids a few sharp lines dominating the estimate.
    ordered = np.sort(sigmas)
    trim = int(n * 0.1)
    kept = ordered[trim:n-trim] if trim else ordered
    return float(np.mean(kept)), n * block_size


def estimate_automatic_snr(wavelengths_nm, intensities_counts, *, block_size=16,
                           noise_method='std', full_scale_counts=float('nan')):
    try:
        x, y = _prepare_xy(wavelengths_nm, intensities_counts)
        sigma, n_noise = block_noise(x, y, block_size, noise_method)
    except (ValueError, FloatingPointError) as exc:
        return SNRMetrics.invalid(str(exc))
    if not math.isfinite(sigma) or sigma <= np.finfo(float).eps * max(1.0, float(np.max(np.abs(y)))):
        return SNRMetrics.invalid('No resolved noise; automatic SNR is undefined')
    # Fixed nine-pixel symmetric smoother affects only the peak estimate, never noise.
    kernel = np.array([1, 2, 3, 4, 5, 4, 3, 2, 1], dtype=float) / 25.0
    smoothed = np.convolve(np.pad(y, 4, mode='reflect'), kernel, mode='valid')
    baseline = float(np.percentile(smoothed, 10))
    signal = smoothed - baseline
    peak = max(0.0, float(np.max(signal)))
    weights = np.empty_like(x)
    weights[0] = (x[1] - x[0]) / 2
    weights[-1] = (x[-1] - x[-2]) / 2
    weights[1:-1] = (x[2:] - x[:-2]) / 2
    integrated = float(np.sum(weights * (y - baseline)))
    noise_integrated = sigma * float(np.sqrt(np.sum(weights * weights)))
    fraction = float(np.max(y)) / full_scale_counts if math.isfinite(full_scale_counts) and full_scale_counts > 0 else float('nan')
    return SNRMetrics(valid=True,
        message=f'Auto {block_size}-pixel detrended {noise_method}; 10% trimmed block mean; peak above smoothed 10th percentile',
        peak_snr=peak/sigma, integrated_snr=integrated/noise_integrated,
        noise_sigma_counts=sigma, peak_signal_counts=peak,
        integrated_signal_counts_nm=integrated, integrated_noise_counts_nm=noise_integrated,
        mean_signal_counts=float(np.mean(y - baseline)), baseline_at_signal_center_counts=baseline,
        peak_fraction_of_full_scale=fraction, n_signal_pixels=len(y), n_noise_pixels=n_noise)
