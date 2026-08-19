from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np

from core.laser_models import LaserCalibrationPoint
from core.time_utils import utc_now_iso
from io_utils.atomic import atomic_text_writer
from planning.power_scan import CalibrationCurve


def save_calibration_csv(
    path: Path,
    *,
    calibration: CalibrationCurve,
    points: list[LaserCalibrationPoint] | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    """Save a calibration curve and, when available, its acquisition details."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with atomic_text_writer(output) as file:
        writer = csv.writer(file)
        writer.writerow(["# file_type", "laser_power_calibration"])
        writer.writerow(["# saved_utc", utc_now_iso()])
        writer.writerow(["# filter_state", calibration.filter_state])

        for key, value in dict(metadata or {}).items():
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

        if points:
            for point in points:
                writer.writerow(
                    [
                        f"{point.setpoint_w:.12e}",
                        f"{point.measured_power_mean_w:.12e}",
                        f"{point.measured_power_std_w:.12e}",
                        int(point.n_reads),
                        point.timestamp_utc,
                        point.port,
                        point.box_id,
                        int(point.channel),
                        f"{point.wavelength_nm:.12e}",
                        point.filter_state,
                    ]
                )
            return

        for setpoint, measured in zip(
            calibration.setpoint_w,
            calibration.measured_power_w,
            strict=True,
        ):
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


def _float_or_nan(value: str | None) -> float:
    try:
        return float(value or "nan")
    except (TypeError, ValueError):
        return float("nan")


def _int_or_default(value: str | None, default: int) -> int:
    try:
        return int(float(value or str(default)))
    except (TypeError, ValueError):
        return int(default)


def load_calibration_csv(
    path: Path,
) -> tuple[CalibrationCurve, list[LaserCalibrationPoint]]:
    """Load a calibration curve and any per-point acquisition metadata."""

    input_path = Path(path)
    points: list[LaserCalibrationPoint] = []
    setpoints: list[float] = []
    measured_powers: list[float] = []
    filter_state = "none"

    with input_path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.reader(file)
        header: list[str] | None = None

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
                header = [value.strip() for value in row]
                continue

            values = {
                header[index]: row[index]
                for index in range(min(len(header), len(row)))
            }
            setpoint = _float_or_nan(values.get("setpoint_W"))
            measured = _float_or_nan(values.get("measured_power_W"))
            setpoints.append(setpoint)
            measured_powers.append(measured)

            point_filter_state = values.get("filter_state", filter_state) or filter_state
            points.append(
                LaserCalibrationPoint(
                    timestamp_utc=values.get("timestamp_utc", ""),
                    port=values.get("port", ""),
                    box_id=values.get("box_id", ""),
                    channel=_int_or_default(values.get("channel"), -1),
                    wavelength_nm=_float_or_nan(values.get("wavelength_nm")),
                    setpoint_w=setpoint,
                    measured_power_mean_w=measured,
                    measured_power_std_w=_float_or_nan(
                        values.get("measured_power_std_W")
                    ),
                    n_reads=_int_or_default(values.get("n_reads"), 0),
                    filter_state=point_filter_state,
                )
            )

    calibration = CalibrationCurve(
        setpoint_w=np.asarray(setpoints, dtype=float),
        measured_power_w=np.asarray(measured_powers, dtype=float),
        filter_state=filter_state,
    )

    # Preserve metadata-only rows only when they describe finite calibration data.
    points = [
        point
        for point in points
        if math.isfinite(point.setpoint_w)
        and math.isfinite(point.measured_power_mean_w)
    ]
    return calibration, points
