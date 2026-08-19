import math

import numpy as np
import pytest

from planning.power_scan import CalibrationCurve, make_power_scan_plan, make_requested_powers_w


def test_requested_power_spacing() -> None:
    assert make_requested_powers_w(
        start_w=1.0,
        stop_w=3.0,
        n_points=3,
        spacing="linear",
    ) == [1.0, 2.0, 3.0]
    values = make_requested_powers_w(
        start_w=1.0,
        stop_w=100.0,
        n_points=3,
        spacing="logarithmic",
    )
    assert np.allclose(values, [1.0, 10.0, 100.0])


def test_calibration_sorts_and_collapses_duplicate_setpoints() -> None:
    curve = CalibrationCurve(
        setpoint_w=np.array([2.0, 1.0, 1.0]),
        measured_power_w=np.array([20.0, 9.0, 11.0]),
    )
    assert np.allclose(curve.setpoint_w, [1.0, 2.0])
    assert np.allclose(curve.measured_power_w, [10.0, 20.0])


def test_expected_actual_outside_calibration_fails() -> None:
    curve = CalibrationCurve(
        setpoint_w=np.array([1.0, 2.0]),
        measured_power_w=np.array([10.0, 20.0]),
    )
    with pytest.raises(ValueError, match="outside the calibration range"):
        make_power_scan_plan(
            requested_powers_w=[25.0],
            basis="expected_actual",
            laser_min_setpoint_w=0.5,
            laser_max_setpoint_w=3.0,
            calibration=curve,
        )


def test_clipped_setpoint_is_preserved_as_warning() -> None:
    plan = make_power_scan_plan(
        requested_powers_w=[0.1, 5.0],
        basis="setpoint",
        laser_min_setpoint_w=1.0,
        laser_max_setpoint_w=4.0,
        allow_clipping=True,
    )
    assert [point.setpoint_w for point in plan.points] == [1.0, 4.0]
    assert len(plan.warnings) == 2


def test_nonfinite_requested_power_fails() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        make_power_scan_plan(
            requested_powers_w=[math.nan],
            basis="setpoint",
            laser_min_setpoint_w=0.0,
            laser_max_setpoint_w=1.0,
        )


def test_inverse_calibration_handles_measured_power_plateau_deterministically() -> None:
    curve = CalibrationCurve(
        setpoint_w=np.array([1.0, 2.0, 3.0]),
        measured_power_w=np.array([10.0, 10.0, 30.0]),
    )

    # The two setpoints on the 10 W plateau are averaged for the inverse map.
    assert curve.setpoint_for_expected_power(10.0) == pytest.approx(1.5)


def test_flat_calibration_is_invalid() -> None:
    with pytest.raises(ValueError, match="distinct measured powers"):
        CalibrationCurve(
            setpoint_w=np.array([1.0, 2.0]),
            measured_power_w=np.array([10.0, 10.0]),
        )
