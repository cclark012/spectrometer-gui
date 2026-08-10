from __future__ import annotations

import csv
from pathlib import Path

from core.records import PowerTracePoint
from core.settings import PowerMonitorSettings
from core.time_utils import utc_now_iso
from io_utils.atomic import atomic_text_writer


def save_power_trace_csv(
    path: Path,
    points: list[PowerTracePoint],
    settings: PowerMonitorSettings,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    max_channels = max((len(point.powers_w) for point in points), default=0)
    header = ["timestamp_utc", "elapsed_s"] + [
        f"ch{index + 1}_power_W" for index in range(max_channels)
    ]

    with atomic_text_writer(path) as file:
        writer = csv.writer(file)
        writer.writerow(["# file_type", "power_trace"])
        writer.writerow(["# saved_utc", utc_now_iso()])
        writer.writerow(["# max_points", settings.max_points])
        writer.writerow(["# polling_interval_ms", settings.interval_ms])
        writer.writerow(header)
        for point in points:
            row = [point.timestamp_utc, f"{point.elapsed_s:.9f}"]
            row.extend(
                f"{point.powers_w[index]:.12e}"
                if index < len(point.powers_w)
                else ""
                for index in range(max_channels)
            )
            writer.writerow(row)
