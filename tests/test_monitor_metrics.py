import numpy as np

from core.records import PowerSnapshot, SpectrumRecord
from processing.monitor_metrics import MonitorCaptureConfig, build_monitor_point, monitor_y


def test_builds_monitor_metrics() -> None:
    record = SpectrumRecord(
        timestamp_utc="now",
        timestamp_s=20.0,
        wavelengths_nm=np.array([400.0, 500.0, 600.0]),
        intensities_counts=np.array([1.0, 3.0, 1.0]),
        p_before=PowerSnapshot([2.0], [0x118]),
        p_after=PowerSnapshot([2.0], [0x118]),
        integration_ms=100,
        averages=1,
        boxcar_width=0,
        correct_dark=False,
        correct_nonlinearity=False,
        field_value=10.0,
    )
    point = build_monitor_point(
        record,
        MonitorCaptureConfig(
            target_wavelength_nm=500.0,
            integration_start_nm=400.0,
            integration_stop_nm=600.0,
            application_t0_s=10.0,
        ),
    )
    assert point.elapsed_s == 10.0
    assert point.intensity_target_counts == 3.0
    value, _, _ = monitor_y(point, "Intensity at captured wavelength / power ch1")
    assert value == 1.5


def test_target_outside_spectral_range_is_nan() -> None:
    record = SpectrumRecord(
        timestamp_utc="now",
        timestamp_s=0.0,
        wavelengths_nm=np.array([400.0, 500.0, 600.0]),
        intensities_counts=np.array([1.0, 3.0, 1.0]),
        p_before=PowerSnapshot([1.0], [0x118]),
        p_after=PowerSnapshot([1.0], [0x118]),
        integration_ms=100,
        averages=1,
        boxcar_width=0,
        correct_dark=False,
        correct_nonlinearity=False,
        field_value=0.0,
    )
    point = build_monitor_point(
        record,
        MonitorCaptureConfig(
            target_wavelength_nm=700.0,
            integration_start_nm=400.0,
            integration_stop_nm=600.0,
            application_t0_s=0.0,
        ),
    )
    assert np.isnan(point.intensity_target_counts)


def test_monitor_metrics_are_independent_of_wavelength_order() -> None:
    record = SpectrumRecord(
        timestamp_utc="now",
        timestamp_s=5.0,
        wavelengths_nm=np.array([600.0, 500.0, 400.0]),
        intensities_counts=np.array([1.0, 3.0, 1.0]),
        p_before=PowerSnapshot([1.0], [0x118]),
        p_after=PowerSnapshot([1.0], [0x118]),
        integration_ms=100,
        averages=1,
        boxcar_width=0,
        correct_dark=False,
        correct_nonlinearity=False,
        field_value=0.0,
    )
    point = build_monitor_point(
        record,
        MonitorCaptureConfig(
            target_wavelength_nm=500.0,
            integration_start_nm=400.0,
            integration_stop_nm=600.0,
            application_t0_s=0.0,
        ),
    )
    assert point.intensity_target_counts == 3.0
    assert point.integrated_range_counts_nm == 400.0
    assert point.total_integrated_counts_nm == 400.0
    assert point.peak_wavelength_nm == 500.0
