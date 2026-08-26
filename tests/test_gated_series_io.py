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
    )
    path = tmp_path / "series.csv"

    save_gated_series_csv(path, series)

    text = path.read_text(encoding="utf-8")
    assert "# file_type,gated_averaged_series" in text
    assert "delay_5_ms__mean_counts" in text
    assert "call_start_std_ms" in text
    assert "std_power_W..." in text
    assert text.count("# trace,") == 1
