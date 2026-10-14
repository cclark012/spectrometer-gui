from __future__ import annotations

from dataclasses import replace

import numpy as np

from core.gated_acquisition import GatedFrameMetadata
from core.records import AcquisitionTiming, PowerSnapshot, SpectrumRecord
from processing.gated_averaging import GatedSeriesAccumulator


def _frame(value: float, *, repeat: int, observed_ms: float) -> SpectrumRecord:
    return SpectrumRecord(
        timestamp_utc="2026-08-21T00:00:00+00:00",
        timestamp_s=0.0,
        wavelengths_nm=np.asarray([500.0, 501.0]),
        intensities_counts=np.asarray([value, 2.0 * value]),
        p_before=PowerSnapshot([1.0e-3], [0x118]),
        p_after=PowerSnapshot([3.0e-3], [0x118]),
        integration_ms=10,
        averages=1,
        boxcar_width=0,
        correct_dark=False,
        correct_nonlinearity=False,
        field_value=0.0,
        power_channels=(0,),
        spectrometer_backend="andor",
        spectrometer_model="DU401_BVF + Kymera",
        spectrometer_serial="26970",
        spectrograph_serial="KY-4444",
        acquisition_timing=AcquisitionTiming(
            discarded_frames_for_target=1,
        ),
        instrument_state={"grating": 1},
        software_provenance={"source_revision": "2026-09-10"},
        background_timestamp_utc="2026-08-20T00:00:00+00:00",
        background_signature="sha256:test",
        gated=GatedFrameMetadata(
            sequence_id="sequence",
            mode="interleaved_decay",
            frame_index=repeat,
            frame_count=2,
            cycle_index=repeat,
            label="delay_5_ms",
            laser_state="off",
            requested_delay_ms=5,
            request_elapsed_since_transition_ms=observed_ms,
            acquisition_call_start_elapsed_ms=observed_ms + 1.0,
            acquisition_call_midpoint_elapsed_ms=observed_ms + 6.0,
            acquisition_call_end_elapsed_ms=observed_ms + 11.0,
            repeat_index=repeat,
        ),
    )


def test_repeated_frames_are_incrementally_averaged() -> None:
    accumulator = GatedSeriesAccumulator()
    accumulator.add(_frame(10.0, repeat=0, observed_ms=5.5))
    accumulator.add(_frame(14.0, repeat=1, observed_ms=6.5))

    series = accumulator.finish()
    trace = series.traces[0]

    assert trace.sample_count == 2
    np.testing.assert_allclose(trace.mean_counts, [12.0, 24.0])
    np.testing.assert_allclose(trace.std_counts, [np.sqrt(8.0), np.sqrt(32.0)])
    assert trace.request_timing.mean_ms == 6.0
    assert trace.request_timing.median_ms == 6.0
    assert np.isclose(trace.request_timing.p95_ms, 6.45)
    assert np.isclose(trace.request_timing.p99_ms, 6.49)
    assert trace.mean_power_w == (2.0e-3,)
    assert series.power_channels == (0,)
    assert series.spectrometer_serial == "26970"
    assert series.instrument_state == {"grating": 1}
    assert series.software_provenance == {"source_revision": "2026-09-10"}
    assert series.background_signature == "sha256:test"
    assert series.detector_frames_discarded_for_target == 2


def test_wavelength_grid_changes_are_rejected() -> None:
    accumulator = GatedSeriesAccumulator()
    first = _frame(10.0, repeat=0, observed_ms=5.0)
    accumulator.add(first)

    changed = replace(first, wavelengths_nm=np.asarray([500.0, 502.0]))
    try:
        accumulator.add(changed)
    except ValueError as exc:
        assert "wavelength grid changed" in str(exc)
    else:
        raise AssertionError("Expected changed wavelength grid to be rejected")
