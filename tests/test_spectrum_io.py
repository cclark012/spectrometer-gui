from __future__ import annotations

import numpy as np

from core.gated_acquisition import GatedFrameMetadata
from core.records import PowerSnapshot, SpectrumRecord
from core.snr_records import SNRMetrics
from io_utils.spectrum_io import (
    load_spectrum_csv,
    load_spectrum_record,
    save_spectrum_record,
)


def _record() -> SpectrumRecord:
    return SpectrumRecord(
        timestamp_utc="2026-01-01T00:00:00+00:00",
        timestamp_s=123.0,
        wavelengths_nm=np.asarray([500.0, 501.0]),
        intensities_counts=np.asarray([10.0, 20.0]),
        p_before=PowerSnapshot([1e-3], [0x118], 0),
        p_after=PowerSnapshot([1.1e-3], [0x118], 0),
        integration_ms=100,
        averages=2,
        boxcar_width=0,
        correct_dark=True,
        correct_nonlinearity=True,
        field_value=10.0,
        run_identifier="run-A",
        notes="line one, with comma\nline two contains literal \\n text",
        spectrometer_backend="andor",
        spectrometer_model="DU401_BVF + Kymera",
        spectrometer_serial="26970",
        spectrograph_serial="KY-4444",
        raw_intensities_counts=np.asarray([11.0, 21.0]),
        scan_active=True,
        scan_index=1,
        scan_count=3,
        laser_channel=2,
        laser_wavelength_nm=532.0,
        filter_state="W1:open",
        gated=GatedFrameMetadata(
            sequence_id="gate-1",
            mode="delayed_after_off",
            frame_index=2,
            frame_count=5,
            cycle_index=0,
            label="delay_50_ms",
            laser_state="off",
            requested_delay_ms=50,
            request_elapsed_since_transition_ms=52.25,
            acquisition_call_start_elapsed_ms=53.0,
            acquisition_call_midpoint_elapsed_ms=103.0,
            acquisition_call_end_elapsed_ms=153.0,
            exposure_window_start_elapsed_ms=53.2,
            exposure_window_end_elapsed_ms=62.9,
            exposure_midpoint_estimate_elapsed_ms=58.05,
            exposure_timing_uncertainty_ms=4.85,
            exposure_timing_basis="driver_call_bounds",
            exposure_sample_windows_elapsed_ms=((53.2, 62.9),),
            timing_error_ms=8.05,
            timing_quality="accepted",
            timing_center_ms=7.9,
            timing_robust_sigma_ms=0.2,
            timing_threshold_ms=4.85,
            phase_index=2,
            repeat_index=1,
        ),
        snr=SNRMetrics(
            valid=True,
            message="ok",
            peak_snr=12.5,
            integrated_snr=20.5,
            noise_sigma_counts=2.5,
            peak_signal_counts=125.0,
            integrated_signal_counts_nm=500.0,
            integrated_noise_counts_nm=25.0,
            mean_signal_counts=100.0,
            baseline_at_signal_center_counts=3.0,
            peak_fraction_of_full_scale=0.25,
            n_signal_pixels=8,
            n_noise_pixels=12,
        ),
    )


def test_spectrum_csv_round_trip(tmp_path):
    record = _record()
    path = tmp_path / "spectrum.csv"
    save_spectrum_record(path, record)

    wavelengths, intensities = load_spectrum_csv(path)
    loaded = load_spectrum_record(path)

    assert np.allclose(wavelengths, record.wavelengths_nm)
    assert np.allclose(intensities, record.intensities_counts)
    assert np.allclose(loaded.wavelengths_nm, record.wavelengths_nm)
    assert np.allclose(loaded.intensities_counts, record.intensities_counts)
    assert np.allclose(loaded.raw_intensities_counts, record.raw_intensities_counts)
    assert loaded.integration_ms == 100
    assert loaded.averages == 2
    assert loaded.run_identifier == "run-A"
    assert loaded.notes == record.notes
    assert loaded.spectrometer_backend == "andor"
    assert loaded.spectrometer_serial == "26970"
    assert loaded.scan_active is True
    assert loaded.laser_channel == 2
    assert loaded.filter_state == "W1:open"
    assert loaded.p_before.pm_status == [0x118]
    assert loaded.gated == record.gated
    assert loaded.snr == record.snr

    text = path.read_text(encoding="utf-8")
    assert "# schema_version,2" in text
    assert "intensity_counts_raw" in text
    assert "intensity_counts_processed" in text
    assert "# p_before_command_status" in text
    assert "# notes_json" not in text
    assert "# scan_active" not in text
    assert "# gated_active" not in text


def test_legacy_spectrum_without_integration_has_nan_counts_per_s(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("wavelength_nm,intensity_counts\n500,10\n501,20\n", encoding="utf-8")

    record = load_spectrum_record(path)

    assert record.integration_ms == 0
    assert np.all(np.isnan(record.intensities_counts_per_s()))


def test_mean_power_snapshot_preserves_latest_range_and_combines_flags():
    record = _record()
    record.p_before = PowerSnapshot([1.0], [0x118 | 0x04], 0)
    record.p_after = PowerSnapshot([3.0], [0x128 | 0x02], 0)

    snapshot = record.mean_power_snapshot()

    assert snapshot.powers_w == [2.0]
    assert snapshot.pm_status == [(0x128 & ~0x0F) | 0x0E]


def test_invalid_snr_round_trips_as_boolean_false(tmp_path):
    record = _record()
    record.snr = SNRMetrics.invalid("not enough noise pixels")
    path = tmp_path / "invalid-snr.csv"

    save_spectrum_record(path, record)
    loaded = load_spectrum_record(path)

    assert loaded.snr is not None
    assert loaded.snr.valid is False
    assert loaded.snr.message == "not enough noise pixels"
    text = path.read_text(encoding="utf-8")
    assert "# snr_status,invalid" in text
    assert "# snr_reason,not enough noise pixels" in text
    assert "# snr_peak," not in text


def test_ordinary_schema_omits_unrelated_optional_blocks(tmp_path):
    record = _record()
    record.scan_active = False
    record.gated = None
    record.snr = None
    record.p_before = PowerSnapshot.missing()
    record.p_after = PowerSnapshot.missing()
    record.laser_port = ""
    record.laser_box_id = ""
    record.laser_channel = -1
    record.raw_intensities_counts = record.intensities_counts.copy()
    path = tmp_path / "ordinary.csv"

    save_spectrum_record(path, record)
    text = path.read_text(encoding="utf-8")

    assert "# p_before_W" not in text
    assert "# scan_index" not in text
    assert "# laser_channel" not in text
    assert "# gated_sequence_id" not in text
    assert "# snr_status" not in text
    assert "intensity_counts_processed" not in text


def test_schema_v2_notes_preserve_whitespace_and_literal_backslash_n(tmp_path):
    record = _record()
    record.notes = "  first line\nsecond has literal \\n text  "
    path = tmp_path / "notes.csv"

    save_spectrum_record(path, record)

    assert load_spectrum_record(path).notes == record.notes
