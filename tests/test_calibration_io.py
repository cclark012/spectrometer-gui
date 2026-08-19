from __future__ import annotations

import numpy as np

from core.laser_models import LaserCalibrationPoint
from io_utils.calibration_io import load_calibration_csv, save_calibration_csv
from planning.power_scan import CalibrationCurve


def test_calibration_csv_round_trip(tmp_path):
    curve = CalibrationCurve(
        setpoint_w=np.asarray([1e-3, 2e-3, 3e-3]),
        measured_power_w=np.asarray([8e-4, 1.6e-3, 2.4e-3]),
    )
    points = [
        LaserCalibrationPoint(
            timestamp_utc="2026-01-01T00:00:00+00:00",
            port="COM3",
            box_id="BOX-1",
            channel=2,
            wavelength_nm=532.0,
            setpoint_w=float(setpoint),
            measured_power_mean_w=float(measured),
            measured_power_std_w=1e-6,
            n_reads=3,
        )
        for setpoint, measured in zip(
            curve.setpoint_w,
            curve.measured_power_w,
            strict=True,
        )
    ]

    path = tmp_path / "calibration.csv"
    save_calibration_csv(path, calibration=curve, points=points)
    loaded_curve, loaded_points = load_calibration_csv(path)

    assert np.allclose(loaded_curve.setpoint_w, curve.setpoint_w)
    assert np.allclose(loaded_curve.measured_power_w, curve.measured_power_w)
    assert loaded_points == points
