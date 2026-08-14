from __future__ import annotations

import numpy as np

from processing.snr import estimate_snr, suggest_acquisition


def test_estimate_snr_detects_gaussian_band() -> None:
    rng = np.random.default_rng(1)
    wavelength = np.linspace(400.0, 900.0, 1001)
    signal = 500.0 * np.exp(-0.5 * ((wavelength - 620.0) / 25.0) ** 2)
    spectrum = 100.0 + 0.02 * (wavelength - 650.0) + signal
    spectrum += rng.normal(0.0, 5.0, size=wavelength.size)
    result = estimate_snr(
        wavelength,
        spectrum,
        signal_start_nm=540.0,
        signal_stop_nm=700.0,
        noise_intervals_nm=[(420.0, 500.0), (760.0, 860.0)],
        baseline_order=1,
        full_scale_counts=65535.0,
    )
    assert result.valid
    assert result.peak_snr > 70
    assert result.integrated_snr > result.peak_snr
    assert 3.0 < result.noise_sigma_counts < 7.0


def test_estimate_snr_handles_descending_wavelengths() -> None:
    wavelength = np.linspace(900.0, 400.0, 501)
    spectrum = 20.0 + 200.0 * np.exp(-0.5 * ((wavelength - 650.0) / 20.0) ** 2)
    result = estimate_snr(
        wavelength,
        spectrum,
        signal_start_nm=580.0,
        signal_stop_nm=720.0,
        noise_intervals_nm=[(410.0, 500.0), (800.0, 890.0)],
    )
    assert result.valid


def test_acquisition_suggestion_respects_limits() -> None:
    wavelength = np.linspace(400.0, 900.0, 1001)
    spectrum = 50.0 + 100.0 * np.exp(-0.5 * ((wavelength - 620.0) / 25.0) ** 2)
    result = estimate_snr(
        wavelength,
        spectrum,
        signal_start_nm=540.0,
        signal_stop_nm=700.0,
        noise_intervals_nm=[(420.0, 500.0), (760.0, 860.0)],
        full_scale_counts=65535.0,
    )
    suggestion = suggest_acquisition(
        result=result,
        metric="peak",
        current_integration_ms=100,
        current_averages=1,
        target_snr=100.0,
        target_peak_fraction=0.75,
        minimum_integration_ms=8,
        maximum_integration_ms=5000,
        maximum_averages=20,
        maximum_total_acquisition_s=10.0,
    )
    assert 8 <= suggestion.integration_ms <= 5000
    assert 1 <= suggestion.averages <= 20
    assert suggestion.integration_ms * suggestion.averages <= 10_000
