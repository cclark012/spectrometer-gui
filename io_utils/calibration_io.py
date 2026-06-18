# io_utils/calibration_io.py

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from core.time_utils import utc_now_iso
from planning.power_scan import CalibrationCurve


def save_calibration_csv(
    path: Path,
    *,
    calibration: CalibrationCurve,
    rows: list[dict] | None = None,
    metadata: dict | None = None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    metadata = dict(metadata or {})

    with path.open("w", newline="") as f:
        writer = csv.writer(f)

        writer.writerow(["# file_type", "laser_power_calibration"])
        writer.writerow(["# saved_utc", utc_now_iso()])
        writer.writerow(["# filter_state", calibration.filter_state])

        for key, value in metadata.items():
            writer.writerow([f"# {key}", value])

        writer.writerow(
            [
                "setpoint_W",
                "measured_power_W",
                "measured_power_std_W",
                "n_reads",
                "timestamp_utc",
                "port",
                "box_id",
                "channel",
                "wavelength_nm",
                "filter_state",
            ]
        )

        if rows:
            for row in rows:
                writer.writerow(
                    [
                        f"{float(row.get('setpoint_w', float('nan'))):.12e}",
                        f"{float(row.get('measured_power_mean_w', float('nan'))):.12e}",
                        f"{float(row.get('measured_power_std_w', float('nan'))):.12e}",
                        int(row.get("n_reads", 0)),
                        row.get("timestamp_utc", ""),
                        row.get("port", ""),
                        row.get("box_id", ""),
                        row.get("channel", ""),
                        row.get("wavelength_nm", ""),
                        row.get("filter_state", calibration.filter_state),
                    ]
                )
        else:
            for setpoint, measured in zip(calibration.setpoint_w, calibration.measured_power_w): # noqa
                writer.writerow(
                    [
                        f"{float(setpoint):.12e}",
                        f"{float(measured):.12e}",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        calibration.filter_state,
                    ]
                )


def load_calibration_csv(path: Path) -> tuple[CalibrationCurve, list[dict]]:
    path = Path(path)

    rows: list[dict] = []
    setpoints = []
    measured = []
    filter_state = "none"

    with path.open("r", newline="") as f:
        reader = csv.reader(f)

        header = None

        for row in reader:
            if not row:
                continue

            first = row[0].strip()

            if first.startswith("#"):
                key = first.lstrip("#").strip()

                if key == "filter_state" and len(row) > 1:
                    filter_state = row[1].strip() or "none"

                continue

            if header is None:
                header = [x.strip() for x in row]
                continue

            values = {header[i]: row[i] for i in range(min(len(header), len(row)))}

            setpoint = float(values.get("setpoint_W", "nan"))
            measured_power = float(values.get("measured_power_W", "nan"))

            setpoints.append(setpoint)
            measured.append(measured_power)

            rows.append(
                {
                    "setpoint_w": setpoint,
                    "measured_power_mean_w": measured_power,
                    "measured_power_std_w": float(values.get("measured_power_std_W", "nan") or "nan"), # noqa
                    "n_reads": int(float(values.get("n_reads", "0") or "0")),
                    "timestamp_utc": values.get("timestamp_utc", ""),
                    "port": values.get("port", ""),
                    "box_id": values.get("box_id", ""),
                    "channel": values.get("channel", ""),
                    "wavelength_nm": values.get("wavelength_nm", ""),
                    "filter_state": values.get("filter_state", filter_state),
                }
            )

    calibration = CalibrationCurve(
        setpoint_w=np.asarray(setpoints, dtype=float),
        measured_power_w=np.asarray(measured, dtype=float),
        filter_state=filter_state,
    )

    return calibration, rows
