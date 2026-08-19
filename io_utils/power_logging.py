# power_logging.py

from __future__ import annotations

import csv
from pathlib import Path

from core.records import PowerTracePoint
from core.time_utils import utc_now_iso


class FullPowerLogger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self.file = self.path.open("w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        self.closed = False

        self.writer.writerow(["# file_type", "full_power_log"])
        self.writer.writerow(["# started_utc", utc_now_iso()])
        self.writer.writerow(
            [
                "timestamp_utc",
                "elapsed_s",
                "source",
                "command_status",
                "ch1_power_W",
                "ch2_power_W",
                "ch1_pm_status",
                "ch2_pm_status",
            ]
        )
        self.file.flush()

    def write_point(self, point: PowerTracePoint) -> None:
        if self.closed:
            return

        ch1 = point.powers_w[0] if len(point.powers_w) >= 1 else ""
        ch2 = point.powers_w[1] if len(point.powers_w) >= 2 else ""
        st1 = point.pm_status[0] if len(point.pm_status) >= 1 else ""
        st2 = point.pm_status[1] if len(point.pm_status) >= 2 else ""

        self.writer.writerow(
            [
                point.timestamp_utc,
                f"{point.elapsed_s:.9f}",
                point.source,
                point.command_status,
                f"{ch1:.12e}" if ch1 != "" else "",
                f"{ch2:.12e}" if ch2 != "" else "",
                st1,
                st2,
            ]
        )
        self.file.flush()

    def close(self) -> None:
        if self.closed:
            return

        self.writer.writerow(["# stopped_utc", utc_now_iso()])
        self.file.flush()
        self.file.close()
        self.closed = True
