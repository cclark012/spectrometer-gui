from __future__ import annotations

import numpy as np

from core.gated_acquisition import GatedFrameMetadata
from core.records import AcquisitionTiming, PowerSnapshot, SpectrumRecord
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
        p_before=PowerSnapshot([1e-3, 2e-3], [0x118, 0x118], 0),
        p_after=PowerSnapshot([1.1e-3, 2.1e-3], [0x118, 0x118], 0),
        integration_ms=100,
        requested_integration_ms=95,
        averages=2,
        boxcar_width=0,
        correct_dark=True,
        correct_nonlinearity=True,
        field_value=10.0,
        power_channels=(1,),
        acquisition_timing=AcquisitionTiming(
            basis="sdk_completion_minus_readout_estimate",
            exposure_duration_s=0.1,
            readout_duration_s=0.01326,
            keep_clean_duration_s=0.08,
            kinetic_cycle_s=0.11326,
            discarded_frames_for_target=1,
        ),
        run_identifier="run-A",
        notes="line one, with comma\nline two contains literal \\n text",
        spectrometer_backend="andor",
        spectrometer_model="DU401_BVF + Kymera",
        spectrometer_serial="26970",
        spectrograph_serial="KY-4444",
        instrument_state={
            "grating": 1,
            "center_wavelength_nm": 650.0,
            "horizontal_readout_mhz": 0.1,
            "vertical_shift_us": 16.25,
            "horizontal_binning": 1,
        },
        software_provenance={
            "gui_version": "0.1.0",
            "source_revision": "2026-09-10",
            "adapter": "andor_sdk2_kymera",
        },
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
            transition_timing_basis="laser_command_readback_complete",
            exposure_sample_windows_elapsed_ms=((53.2, 62.9),),
            timing_error_ms=8.05,
            timing_quality="accepted",
            timing_center_ms=7.9,
            timing_robust_sigma_ms=0.2,
            timing_threshold_ms=4.85,
            phase_index=2,
            repeat_index=1,
            repeat_count=3,
            pump_duration_ms=50,
            inter_frame_gap_ms=7,
            inter_repeat_gap_ms=125,
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
    assert loaded.requested_integration_ms == 95
    assert loaded.averages == 2
    assert loaded.run_identifier == "run-A"
    assert loaded.notes == record.notes
    assert loaded.spectrometer_backend == "andor"
    assert loaded.spectrometer_serial == "26970"
    assert loaded.instrument_state == record.instrument_state
    assert loaded.software_provenance == record.software_provenance
    assert loaded.scan_active is True
    assert loaded.laser_channel == 2
    assert loaded.filter_state == "W1:open"
    assert loaded.power_channels == (1,)
    assert np.isnan(loaded.p_before.powers_w[0])
    assert np.isclose(loaded.p_before.powers_w[1], 2.0e-3)
    assert np.isnan(loaded.p_after.powers_w[0])
    assert np.isclose(loaded.p_after.powers_w[1], 2.1e-3)
    # Normal Newport status words are intentionally omitted in schema v4.
    assert loaded.p_before.pm_status == []
    assert loaded.p_before.command_status == 0
    assert loaded.gated == record.gated
    assert loaded.snr == record.snr
    assert loaded.acquisition_timing is not None
    assert loaded.acquisition_timing.basis == record.acquisition_timing.basis
    assert np.isclose(loaded.acquisition_timing.readout_duration_s, 0.01326)
    assert loaded.acquisition_timing.discarded_frames_for_target == 1

    text = path.read_text(encoding="utf-8")
    assert "# schema_version,5" in text
    assert "intensity_counts_raw" in text
    assert "intensity_counts_processed" in text
    assert "# power_status_abnormal" not in text
    assert "# notes_json" not in text
    assert "# scan_active" not in text
    assert "# gated_active" not in text
    assert "# gated_plan_ms,pump=50,frame_gap=7,repeat_gap=125" in text
    assert "discarded_for_target=1" in text
    assert "before_W=[0.002]" in text
    assert "after_W=[0.0021]" in text


def test_only_requested_power_channels_are_persisted(tmp_path):
    record = _record()
    record.power_channels = (0, 1)
    both_path = tmp_path / "both.csv"
    save_spectrum_record(both_path, record)
    both_text = both_path.read_text(encoding="utf-8")
    assert "channels=[1,2]" in both_text
    assert "before_W=[0.001,0.002]" in both_text

    record.power_channels = (0,)
    first_path = tmp_path / "first.csv"
    save_spectrum_record(first_path, record)
    first_text = first_path.read_text(encoding="utf-8")
    assert "channels=[1]" in first_text
    assert "before_W=[0.001]" in first_text
    assert "0.002" not in first_text.split("# power", 1)[1].splitlines()[0]


def test_smoothing_method_boundary_and_width_round_trip(tmp_path):
    record = _record()
    record.smoothing_window = "hann"
    record.smoothing_boundary = "reflect"
    record.boxcar_width = 9
    path = tmp_path / "smoothed.csv"

    save_spectrum_record(path, record)
    loaded = load_spectrum_record(path)

    assert loaded.smoothing_window == "hann"
    assert loaded.smoothing_boundary == "reflect"
    assert loaded.boxcar_width == 9
    assert "# smoothing,method=hann,width=9,boundary=reflect" in path.read_text(
        encoding="utf-8"
    )


def test_normal_power_status_remains_compact_after_reload_and_resave(tmp_path):
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    save_spectrum_record(first_path, _record())

    save_spectrum_record(second_path, load_spectrum_record(first_path))

    assert "# power_status_abnormal" not in second_path.read_text(encoding="utf-8")


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
    assert "# snr,status=invalid,reason=not enough noise pixels" in text
    assert "# snr_metrics" not in text


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
    assert "# scan," not in text
    assert "# laser," not in text
    assert "# gated_frame," not in text


def test_field_applicability_distinguishes_zero_from_not_recorded(tmp_path):
    zero_record = _record()
    zero_record.field_value = 0.0
    zero_path = tmp_path / "zero-field.csv"
    save_spectrum_record(zero_path, zero_record)
    assert load_spectrum_record(zero_path).field_value == 0.0
    assert "# field_value_mT,0.000000000000e+00" in zero_path.read_text(
        encoding="utf-8"
    )

    omitted_record = _record()
    omitted_record.field_value = None
    omitted_path = tmp_path / "field-not-recorded.csv"
    save_spectrum_record(omitted_path, omitted_record)
    assert load_spectrum_record(omitted_path).field_value is None
    assert "# field_value_mT" not in omitted_path.read_text(encoding="utf-8")


def test_abnormal_power_status_is_retained(tmp_path):
    record = _record()
    record.p_before = PowerSnapshot([-1.0e-6], [0x11C], 2)
    record.power_channels = (0,)
    record.raw_intensities_counts = record.intensities_counts.copy()
    path = tmp_path / "abnormal-power.csv"

    save_spectrum_record(path, record)
    loaded = load_spectrum_record(path)

    text = path.read_text(encoding="utf-8")
    assert "# power_status_abnormal" in text
    assert loaded.p_before.powers_w == [-1.0e-6]
    assert loaded.p_before.pm_status == [0x11C]
    assert loaded.p_before.command_status == 2
    assert "# snr_status" not in text
    assert "intensity_counts_processed" not in text


def test_schema_v2_notes_preserve_whitespace_and_literal_backslash_n(tmp_path):
    record = _record()
    record.notes = "  first line\nsecond has literal \\n text  "
    path = tmp_path / "notes.csv"

    save_spectrum_record(path, record)

    assert load_spectrum_record(path).notes == record.notes


def test_automatic_snr_description_survives_save_load(tmp_path):
    record=_record()
    record.snr=SNRMetrics(valid=True,message='Auto 16-pixel detrended std; peak above smoothed 10th percentile',peak_snr=12)
    path=tmp_path/'automatic_snr.csv'
    save_spectrum_record(path,record)
    restored=load_spectrum_record(path)
    assert restored.snr.message==record.snr.message
