from __future__ import annotations

import csv

from core.records import PowerTracePoint
from core.settings import PowerMonitorSettings
from io_utils.power_trace_io import save_power_trace_csv


def test_power_trace_preserves_source_and_status_words(tmp_path) -> None:
    path = tmp_path / "power.csv"
    points = [
        PowerTracePoint(
            timestamp_utc="2026-08-19T12:00:00+00:00",
            elapsed_s=1.25,
            source="spectrum_mean",
            powers_w=[1.0e-3, 2.0e-3],
            pm_status=[0x118, 0x228],
            command_status=7,
        )
    ]

    save_power_trace_csv(path, points, PowerMonitorSettings())

    rows = list(csv.reader(path.open("r", encoding="utf-8", newline="")))
    header = next(row for row in rows if row and row[0] == "timestamp_utc")
    data = rows[rows.index(header) + 1]
    values = dict(zip(header, data, strict=True))
    assert values["source"] == "spectrum_mean"
    assert values["command_status"] == "7"
    assert values["ch1_pm_status"] == str(0x118)
    assert values["ch2_pm_status"] == str(0x228)
