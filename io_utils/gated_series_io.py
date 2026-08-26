from __future__ import annotations

import csv
from pathlib import Path

from io_utils.atomic import atomic_text_writer
from processing.gated_averaging import GatedSeriesRecord


def _timing_values(statistics) -> list[str]:
    return [
        f"{statistics.mean_ms:.9f}",
        f"{statistics.std_ms:.9f}",
        f"{statistics.minimum_ms:.9f}",
        f"{statistics.maximum_ms:.9f}",
    ]


def save_gated_series_csv(path: Path, series: GatedSeriesRecord) -> None:
    """Save all averaged delay/state traces in one analysis-friendly CSV."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(output) as file:
        writer = csv.writer(file)
        writer.writerow(["# file_type", "gated_averaged_series"])
        writer.writerow(["# sequence_id", series.sequence_id])
        writer.writerow(["# mode", series.mode])
        writer.writerow(["# timestamp_utc", series.timestamp_utc])
        writer.writerow(["# integration_ms", series.integration_ms])
        writer.writerow(["# detector_averages", series.detector_averages])
        writer.writerow(["# field_value_mT", f"{series.field_value_mT:.12e}"])
        writer.writerow(["# laser_port", series.laser_port])
        writer.writerow(["# laser_box_id", series.laser_box_id])
        writer.writerow(["# laser_channel", series.laser_channel])
        writer.writerow(["# laser_wavelength_nm", f"{series.laser_wavelength_nm:.12e}"])
        writer.writerow(
            [
                "# trace_metadata_columns",
                "index",
                "label",
                "laser_state",
                "requested_delay_ms",
                "sample_count",
                "request_mean_ms",
                "request_std_ms",
                "request_min_ms",
                "request_max_ms",
                "call_start_mean_ms",
                "call_start_std_ms",
                "call_start_min_ms",
                "call_start_max_ms",
                "call_midpoint_mean_ms",
                "call_midpoint_std_ms",
                "call_midpoint_min_ms",
                "call_midpoint_max_ms",
                "call_end_mean_ms",
                "call_end_std_ms",
                "call_end_min_ms",
                "call_end_max_ms",
                "mean_power_W...",
                "std_power_W...",
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
                    *[f"{value:.12e}" for value in trace.mean_power_w],
                    *[f"{value:.12e}" for value in trace.std_power_w],
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
