from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from io_utils.atomic import atomic_text_writer
from processing.gated_averaging import GatedSeriesRecord


def _number_text(value: float) -> str | None:
    number = float(value)
    return f"{number:.12g}" if np.isfinite(number) else None


def _write_group(writer, name: str, fields: list[tuple[str, object]]) -> None:
    row = [f"# {name}"]
    for key, value in fields:
        if value is None or (isinstance(value, str) and not value):
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        row.append(f"{key}={value}")
    if len(row) > 1:
        writer.writerow(row)


def _write_mapping_group(writer, name: str, values: dict[str, object]) -> None:
    fields: list[tuple[str, object]] = []
    for key, value in values.items():
        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, sort_keys=True, separators=(",", ":"))
        fields.append((str(key), value))
    _write_group(writer, name, fields)


def _timing_values(statistics) -> list[str]:
    return [
        f"{statistics.mean_ms:.9f}",
        f"{statistics.std_ms:.9f}",
        f"{statistics.minimum_ms:.9f}",
        f"{statistics.maximum_ms:.9f}",
        f"{statistics.median_ms:.9f}",
        f"{statistics.p95_ms:.9f}",
        f"{statistics.p99_ms:.9f}",
    ]


def _timing_headers(prefix: str) -> list[str]:
    return [
        f"{prefix}_{suffix}_ms"
        for suffix in ("mean", "std", "min", "max", "median", "p95", "p99")
    ]


def save_gated_series_csv(path: Path, series: GatedSeriesRecord) -> None:
    """Save all averaged delay/state traces in one analysis-friendly CSV."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(output) as file:
        writer = csv.writer(file)
        writer.writerow(["# file_type", "gated_averaged_series"])
        writer.writerow(["# schema_version", 5])
        writer.writerow(["# sequence_id", series.sequence_id])
        writer.writerow(["# timestamp_utc", series.timestamp_utc])
        _write_group(
            writer,
            "spectrometer",
            [
                ("backend", series.spectrometer_backend),
                ("model", series.spectrometer_model),
                ("serial", series.spectrometer_serial),
                ("spectrograph_serial", series.spectrograph_serial),
            ],
        )
        if series.software_provenance:
            _write_mapping_group(
                writer,
                "software_provenance",
                series.software_provenance,
            )
        if series.spectrometer_backend == "andor" and series.instrument_state:
            _write_mapping_group(writer, "andor_state", series.instrument_state)
        requested_averaging = str(series.averaging_mode)
        applied_averaging = (
            "device" if series.device_averaging_used else "software"
        )
        averaging_fields: list[tuple[str, object]]
        if requested_averaging == applied_averaging:
            averaging_fields = [("averaging", applied_averaging)]
        else:
            averaging_fields = [
                ("averaging_requested", requested_averaging),
                ("averaging_applied", applied_averaging),
            ]
        _write_group(
            writer,
            "acquisition",
            [
                ("integration_ms", _number_text(series.integration_ms)),
                (
                    "integration_requested_ms",
                    _number_text(series.requested_integration_ms)
                    if np.isfinite(float(series.requested_integration_ms))
                    and not np.isclose(
                        float(series.requested_integration_ms),
                        float(series.integration_ms),
                        rtol=0.0,
                        atol=1.0e-6,
                    )
                    else None,
                ),
                ("averages", int(series.detector_averages)),
                *averaging_fields,
            ],
        )
        _write_group(
            writer,
            "detector_corrections",
            [
                ("electric_dark", bool(series.correct_dark)),
                ("nonlinearity", bool(series.correct_nonlinearity)),
            ],
        )
        if series.smoothing_window != "none":
            _write_group(
                writer,
                "smoothing",
                [
                    ("method", series.smoothing_window),
                    ("width", int(series.smoothing_width)),
                    ("boundary", series.smoothing_boundary),
                ],
            )
        _write_group(
            writer,
            "gated_plan_ms",
            [
                ("mode", series.mode),
                ("repeats", int(series.repeat_count)),
                ("pump", series.pump_duration_ms),
                ("frame_gap", series.inter_frame_gap_ms),
                ("repeat_gap", series.inter_repeat_gap_ms),
            ],
        )
        _write_group(
            writer,
            "timing_guard",
            [
                ("method", series.timing_guard_method),
                (
                    "reference",
                    series.timing_reference
                    if series.timing_guard_method == "retain"
                    else None,
                ),
                (
                    "tolerance_ms",
                    _number_text(series.timing_tolerance_ms)
                    if series.timing_guard_method == "retain"
                    else None,
                ),
                ("evaluated", int(series.timing_evaluated_count)),
                (
                    "retained",
                    sum(int(trace.sample_count) for trace in series.traces)
                    if series.timing_guard_method == "retain"
                    else None,
                ),
                ("rejected", int(series.timing_rejected_count)),
                (
                    "attempt_limit",
                    int(series.timing_attempt_limit)
                    if series.timing_guard_method == "retain"
                    else None,
                ),
            ],
        )
        _write_group(
            writer,
            "timing_model",
            [
                ("basis", series.timing_basis),
                ("transition_basis", series.transition_timing_basis),
                ("delay_reference", series.timing_reference),
                ("signed_error", "reference_minus_requested_delay"),
            ],
        )
        if series.detector_frames_discarded_for_target > 0:
            _write_group(
                writer,
                "detector_stream",
                [
                    (
                        "discarded_for_target",
                        int(series.detector_frames_discarded_for_target),
                    )
                ],
            )
        if series.failure_reason:
            writer.writerow(["# incomplete_reason", series.failure_reason])
        if series.field_value_mT is not None and np.isfinite(
            float(series.field_value_mT)
        ):
            writer.writerow(["# field_value_mT", f"{series.field_value_mT:.12e}"])
        _write_group(
            writer,
            "laser",
            [
                ("box_id", series.laser_box_id),
                ("channel", series.laser_channel),
                ("wavelength_nm", _number_text(series.laser_wavelength_nm)),
            ],
        )
        if series.power_channels:
            writer.writerow(
                [
                    "# power_channels",
                    *[int(channel) + 1 for channel in series.power_channels],
                ]
            )
        if series.background_signature or series.background_timestamp_utc:
            _write_group(
                writer,
                "background",
                [
                    ("signature", series.background_signature),
                    ("timestamp_utc", series.background_timestamp_utc),
                ],
            )
        power_headers = [
            f"mean_power_ch{int(channel) + 1}_W"
            for channel in series.power_channels
        ] + [
            f"std_power_ch{int(channel) + 1}_W"
            for channel in series.power_channels
        ]
        writer.writerow(
            [
                "# trace_metadata_columns",
                "index",
                "label",
                "laser_state",
                "requested_delay_ms",
                "sample_count",
                *_timing_headers("request"),
                *_timing_headers("call_start"),
                *_timing_headers("call_midpoint"),
                *_timing_headers("call_end"),
                *_timing_headers("exposure_start"),
                *_timing_headers("exposure_midpoint"),
                *_timing_headers("exposure_end"),
                *_timing_headers("exposure_uncertainty"),
                *_timing_headers("timing_error"),
                *power_headers,
            ]
        )
        for index, trace in enumerate(series.traces):
            writer.writerow(
                [
                    "# trace",
                    index,
                    trace.label,
                    trace.laser_state,
                    trace.requested_delay_ms,
                    trace.sample_count,
                    *_timing_values(trace.request_timing),
                    *_timing_values(trace.acquisition_start_timing),
                    *_timing_values(trace.acquisition_midpoint_timing),
                    *_timing_values(trace.acquisition_end_timing),
                    *_timing_values(trace.exposure_start_timing),
                    *_timing_values(trace.exposure_midpoint_timing),
                    *_timing_values(trace.exposure_end_timing),
                    *_timing_values(trace.exposure_uncertainty),
                    *_timing_values(trace.timing_error),
                    *[
                        f"{float(trace.mean_power_w[channel_index]):.12e}"
                        if channel_index < len(trace.mean_power_w)
                        else "nan"
                        for channel_index in range(len(series.power_channels))
                    ],
                    *[
                        f"{float(trace.std_power_w[channel_index]):.12e}"
                        if channel_index < len(trace.std_power_w)
                        else "nan"
                        for channel_index in range(len(series.power_channels))
                    ],
                ]
            )

        header = ["wavelength_nm"]
        for index, trace in enumerate(series.traces):
            name = trace.label or f"trace_{index}"
            header.extend([f"{name}__mean_counts", f"{name}__std_counts"])
        writer.writerow(header)

        for pixel_index, wavelength_nm in enumerate(series.wavelengths_nm):
            row = [f"{float(wavelength_nm):.12e}"]
            for trace in series.traces:
                row.extend(
                    [
                        f"{float(trace.mean_counts[pixel_index]):.12e}",
                        f"{float(trace.std_counts[pixel_index]):.12e}",
                    ]
                )
            writer.writerow(row)
