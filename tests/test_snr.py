from __future__ import annotations

import numpy as np

from devices.emulated_spectrometer import EmulatedSpectrometer
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


def test_estimate_snr_does_not_collapse_on_lower_clipped_noise() -> None:
    rng = np.random.default_rng(4)
    wavelength = np.linspace(400.0, 900.0, 1001)
    signal = 200.0 * np.exp(-0.5 * ((wavelength - 620.0) / 25.0) ** 2)
    noise = np.maximum(rng.normal(0.0, 2.0, wavelength.size), 0.0)
    spectrum = signal + noise

    result = estimate_snr(
        wavelength,
        spectrum,
        signal_start_nm=550.0,
        signal_stop_nm=700.0,
        noise_intervals_nm=[(410.0, 500.0), (780.0, 890.0)],
        baseline_order=1,
    )

    assert result.valid
    assert result.noise_sigma_counts > 0.25
    assert result.peak_snr < 1000.0


def test_emulator_snr_is_stable_at_fixed_integration_time() -> None:
    spectrometer = EmulatedSpectrometer(random_seed=7)
    peak_snr = []
    for _ in range(40):
        acquisition = spectrometer.acquire_spectrum(
            integration_ms=10,
            averages=1,
            correct_dark=True,
            correct_nonlinearity=True,
        )
        result = estimate_snr(
            acquisition.wavelengths_nm,
            acquisition.intensities_counts,
            signal_start_nm=400.0,
            signal_stop_nm=750.0,
            noise_intervals_nm=[(900.0, 1050.0)],
            baseline_order=1,
            full_scale_counts=spectrometer.max_intensity,
        )
        assert result.valid
        peak_snr.append(result.peak_snr)

    values = np.asarray(peak_snr, dtype=float)
    coefficient_of_variation = float(np.std(values) / np.mean(values))
    assert coefficient_of_variation < 0.25
    assert float(np.max(values) / np.min(values)) < 3.0
