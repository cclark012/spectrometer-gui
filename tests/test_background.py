import numpy as np

from core.records import BackgroundSpectrum
from processing.background import BackgroundCorrector


def test_background_subtraction_same_grid() -> None:
    corrector = BackgroundCorrector()
    corrector.set_background(
        BackgroundSpectrum(
            timestamp_utc="now",
            wavelengths_nm=np.array([400.0, 500.0]),
            counts_per_s=np.array([10.0, 20.0]),
            integration_ms=100,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )
    )
    corrected, applied, timestamp, integration_ms = corrector.apply(
        wavelengths_nm=np.array([400.0, 500.0]),
        intensities_counts=np.array([2.0, 5.0]),
        integration_ms=100,
    )
    assert applied
    assert timestamp == "now"
    assert integration_ms == 100
    assert np.allclose(corrected, [1.0, 3.0])


def test_background_subtraction_interpolates_grid() -> None:
    corrector = BackgroundCorrector()
    corrector.set_background(
        BackgroundSpectrum(
            timestamp_utc="now",
            wavelengths_nm=np.array([400.0, 500.0]),
            counts_per_s=np.array([10.0, 20.0]),
            integration_ms=100,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )
    )
    corrected, *_ = corrector.apply(
        wavelengths_nm=np.array([450.0]),
        intensities_counts=np.array([3.0]),
        integration_ms=100,
    )
    assert np.allclose(corrected, [1.5])


def test_background_cache_checks_full_grid_not_only_endpoints() -> None:
    corrector = BackgroundCorrector()
    corrector.set_background(
        BackgroundSpectrum(
            timestamp_utc="now",
            wavelengths_nm=np.array([400.0, 450.0, 500.0]),
            counts_per_s=np.array([0.0, 10.0, 0.0]),
            integration_ms=100,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )
    )
    first, *_ = corrector.apply(
        wavelengths_nm=np.array([400.0, 450.0, 500.0]),
        intensities_counts=np.ones(3),
        integration_ms=100,
    )
    second, *_ = corrector.apply(
        wavelengths_nm=np.array([400.0, 475.0, 500.0]),
        intensities_counts=np.ones(3),
        integration_ms=100,
    )
    assert not np.allclose(first, second)
    assert np.allclose(second, [1.0, 0.5, 1.0])


def test_background_interpolation_accepts_descending_background_grid() -> None:
    corrector = BackgroundCorrector()
    corrector.set_background(
        BackgroundSpectrum(
            timestamp_utc="now",
            wavelengths_nm=np.array([500.0, 400.0]),
            counts_per_s=np.array([20.0, 10.0]),
            integration_ms=100,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )
    )
    corrected, *_ = corrector.apply(
        wavelengths_nm=np.array([450.0]),
        intensities_counts=np.array([3.0]),
        integration_ms=100,
    )
    assert np.allclose(corrected, [1.5])


def test_background_rejects_mismatched_shapes() -> None:
    corrector = BackgroundCorrector()
    background = BackgroundSpectrum(
        timestamp_utc="now",
        wavelengths_nm=np.array([400.0, 500.0]),
        counts_per_s=np.array([10.0]),
        integration_ms=100,
        averages=1,
        correct_dark=False,
        correct_nonlinearity=False,
    )
    try:
        corrector.set_background(background)
    except ValueError as exc:
        assert "equal shapes" in str(exc)
    else:
        raise AssertionError("Expected mismatched background arrays to fail")


def test_background_rejects_nonpositive_integration_time() -> None:
    corrector = BackgroundCorrector()
    corrector.set_background(
        BackgroundSpectrum(
            timestamp_utc="now",
            wavelengths_nm=np.array([400.0, 500.0]),
            counts_per_s=np.array([10.0, 20.0]),
            integration_ms=100,
            averages=1,
            correct_dark=False,
            correct_nonlinearity=False,
        )
    )
    try:
        corrector.apply(
            wavelengths_nm=np.array([400.0, 500.0]),
            intensities_counts=np.array([1.0, 2.0]),
            integration_ms=0,
        )
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("Expected nonpositive integration time to fail")
