from __future__ import annotations

from pathlib import Path

import numpy as np

from io_utils.gated_series_io import save_gated_series_csv
from processing.gated_averaging import (
    GatedAverageTrace,
    GatedSeriesRecord,
    TimingStatistics,
)


def test_gated_series_is_saved_as_one_matrix_csv(tmp_path: Path) -> None:
    timing = TimingStatistics(5.5, 0.5, 5.0, 6.0)
    series = GatedSeriesRecord(
        sequence_id="sequence",
        mode="interleaved_decay",
        timestamp_utc="2026-08-21T00:00:00+00:00",
        wavelengths_nm=np.asarray([500.0, 501.0]),
        traces=(
            GatedAverageTrace(
                label="delay_5_ms",
                laser_state="off",
                requested_delay_ms=5,
                sample_count=2,
                mean_counts=np.asarray([10.0, 20.0]),
                std_counts=np.asarray([1.0, 2.0]),
                request_timing=timing,
                acquisition_start_timing=timing,
                acquisition_midpoint_timing=timing,
                acquisition_end_timing=timing,
                mean_power_w=(2.0e-3,),
                std_power_w=(1.0e-4,),
            ),
        ),
        integration_ms=10,
        detector_averages=1,
        field_value_mT=0.0,
        laser_port="COM8",
        laser_box_id="box",
        laser_channel=1,
        laser_wavelength_nm=532.0,
        timing_evaluated_count=12,
        timing_rejected_count=2,
        timing_guard_method="discard",
        detector_frames_discarded_for_target=7,
        power_channels=(1,),
        spectrometer_backend="andor",
        spectrometer_model="DU401_BVF + Kymera",
        spectrometer_serial="26970",
        spectrograph_serial="KY-4444",
        instrument_state={"grating": 1, "vertical_shift_us": 16.25},
        software_provenance={"source_revision": "2026-09-10"},
        background_signature="sha256:abc",
        background_timestamp_utc="2026-09-09T23:59:00+00:00",
    )
    path = tmp_path / "series.csv"

    save_gated_series_csv(path, series)

    text = path.read_text(encoding="utf-8")
    assert "# file_type,gated_averaged_series" in text
    assert "# schema_version,5" in text
    assert "delay_5_ms__mean_counts" in text
    assert "call_start_std_ms" in text
    assert "timing_error_p99_ms" in text
    assert "std_power_ch2_W" in text
    assert "# power_channels,2" in text
    assert "# timing_guard,method=discard,evaluated=12,rejected=2" in text
    assert "# detector_stream,discarded_for_target=7" in text
    assert "exposure_uncertainty_mean_ms" in text
    assert "# spectrometer,backend=andor" in text
    assert "# andor_state,grating=1,vertical_shift_us=16.25" in text
    assert "# background,signature=sha256:abc" in text
    assert text.count("# trace,") == 1


def test_retain_timing_policy_is_saved_with_attempt_accounting(tmp_path: Path) -> None:
    timing = TimingStatistics(10.0, 0.1, 9.8, 10.2)
    series = GatedSeriesRecord(
        sequence_id="retained",
        mode="delayed_after_off",
        timestamp_utc="2026-09-14T00:00:00+00:00",
        wavelengths_nm=np.asarray([500.0]),
        traces=(
            GatedAverageTrace(
                label="delay_10_ms",
                laser_state="off",
                requested_delay_ms=10,
                sample_count=100,
                mean_counts=np.asarray([5.0]),
                std_counts=np.asarray([1.0]),
                request_timing=timing,
                acquisition_start_timing=timing,
                acquisition_midpoint_timing=timing,
                acquisition_end_timing=timing,
            ),
        ),
        integration_ms=12.23,
        detector_averages=1,
        field_value_mT=None,
        laser_port="COM8",
        laser_box_id="box",
        laser_channel=1,
        laser_wavelength_nm=532.0,
        timing_evaluated_count=127,
        timing_rejected_count=27,
        timing_guard_method="retain",
        timing_reference="exposure_start",
        timing_basis="continuous_completion_minus_readout_estimate",
        transition_timing_basis="laser_command_readback_complete",
        timing_tolerance_ms=2.0,
        timing_attempt_limit=300,
        repeat_count=100,
    )
    path = tmp_path / "retained.csv"

    save_gated_series_csv(path, series)

    text = path.read_text(encoding="utf-8")
    assert (
        "# timing_guard,method=retain,reference=exposure_start,"
        "tolerance_ms=2,evaluated=127,retained=100,rejected=27,"
        "attempt_limit=300"
    ) in text
    assert (
        "# timing_model,basis=continuous_completion_minus_readout_estimate,"
        "transition_basis=laser_command_readback_complete,"
        "delay_reference=exposure_start,"
        "signed_error=reference_minus_requested_delay"
    ) in text


def test_gated_series_omits_unrecorded_field(tmp_path: Path) -> None:
    series = GatedSeriesRecord(
        sequence_id="sequence",
        mode="delayed_after_off",
        timestamp_utc="2026-09-09T00:00:00+00:00",
        wavelengths_nm=np.asarray([500.0]),
        traces=(),
        integration_ms=10,
        detector_averages=1,
        field_value_mT=None,
        laser_port="COM8",
        laser_box_id="box",
        laser_channel=1,
        laser_wavelength_nm=532.0,
    )
    path = tmp_path / "series-no-field.csv"

    save_gated_series_csv(path, series)

    assert "# field_value_mT" not in path.read_text(encoding="utf-8")
