# planning/power_scan.py

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from core.laser_models import PowerScanPoint


SpacingMode = Literal["linear", "logarithmic", "custom"]
PowerBasis = Literal["setpoint", "expected_actual"]


@dataclass(frozen=True, slots=True)
class CalibrationCurve:
    """
    Maps laser setpoint W -> measured Newport power W.

    The inverse interpolation is used for expected-actual-power scans.
    """

    setpoint_w: np.ndarray
    measured_power_w: np.ndarray
    filter_state: str = "none"

    def __post_init__(self) -> None:
        setpoint = np.asarray(self.setpoint_w, dtype=float)
        measured = np.asarray(self.measured_power_w, dtype=float)

        if setpoint.shape != measured.shape:
            raise ValueError("setpoint_w and measured_power_w must have the same shape")

        if setpoint.size < 2:
            raise ValueError("CalibrationCurve requires at least two points")

        mask = np.isfinite(setpoint) & np.isfinite(measured)
        setpoint = setpoint[mask]
        measured = measured[mask]

        if setpoint.size < 2:
            raise ValueError("CalibrationCurve requires at least two finite points")

        order = np.argsort(setpoint)
        setpoint = setpoint[order]
        measured = measured[order]

        # Collapse duplicate setpoints by averaging measured powers.
        unique_setpoints = []
        unique_measured = []

        for value in np.unique(setpoint):
            idx = setpoint == value
            unique_setpoints.append(float(value))
            unique_measured.append(float(np.mean(measured[idx])))

        setpoint = np.asarray(unique_setpoints, dtype=float)
        measured = np.asarray(unique_measured, dtype=float)

        if setpoint.size < 2:
            raise ValueError("CalibrationCurve requires at least two unique setpoints")

        if not np.all(np.diff(setpoint) > 0):
            raise ValueError("Calibration setpoints must be strictly increasing")

        # For inverse targeting, measured power should be monotonic.
        if not np.all(np.diff(measured) >= 0):
            raise ValueError("Measured calibration power must be monotonic increasing")

        object.__setattr__(self, "setpoint_w", setpoint)
        object.__setattr__(self, "measured_power_w", measured)

    def expected_power(self, setpoint_w: float, *, transmission: float = 1.0) -> float:
        return float(
            np.interp(
                float(setpoint_w),
                self.setpoint_w,
                self.measured_power_w,
            )
        ) * float(transmission)

    def measured_power_bounds(self) -> tuple[float, float]:
        measured = np.asarray(self.measured_power_w, dtype=float)
        measured = measured[np.isfinite(measured)]

        if measured.size == 0:
            return float("nan"), float("nan")

        return float(np.min(measured)), float(np.max(measured))

    def setpoint_for_expected_power(
        self,
        requested_actual_power_w: float,
        *,
        transmission: float = 1.0,
        allow_extrapolation: bool = False,
    ) -> float:
        t = float(transmission)

        if not math.isfinite(t) or t <= 0:
            return float("nan")

        effective_requested = float(requested_actual_power_w) / t

        if not allow_extrapolation:
            p_min, p_max = self.measured_power_bounds()

            if effective_requested < p_min or effective_requested > p_max:
                return float("nan")

        return float(
            np.interp(
                effective_requested,
                self.measured_power_w,
                self.setpoint_w,
            )
        )

def make_requested_powers_w(
    *,
    start_w: float,
    stop_w: float,
    n_points: int,
    spacing: SpacingMode,
    custom_values_w: list[float] | None = None,
) -> list[float]:
    if spacing == "custom":
        if not custom_values_w:
            raise ValueError("custom_values_w is required for custom spacing")

        return [float(x) for x in custom_values_w]

    n = int(n_points)

    if n < 1:
        raise ValueError("n_points must be at least 1")

    start = float(start_w)
    stop = float(stop_w)

    if spacing == "linear":
        return [float(x) for x in np.linspace(start, stop, n)]

    if spacing == "logarithmic":
        if start <= 0 or stop <= 0:
            raise ValueError("logarithmic spacing requires positive start and stop powers")

        return [float(x) for x in np.geomspace(start, stop, n)]

    raise ValueError(f"Unknown spacing mode: {spacing!r}")


def make_power_scan_points(
    *,
    requested_powers_w: list[float],
    basis: PowerBasis,
    laser_min_setpoint_w: float,
    laser_max_setpoint_w: float,
    calibration: CalibrationCurve | None = None,
    transmission: float = 1.0,
    filter_state: str = "none",
    allow_clipping: bool = True,
) -> list[PowerScanPoint]:
    points: list[PowerScanPoint] = []

    laser_min = float(laser_min_setpoint_w)
    laser_max = float(laser_max_setpoint_w)
    t = float(transmission)

    if not math.isfinite(t) or t <= 0:
        raise ValueError(f"Invalid transmission: {transmission!r}")

    for i, requested in enumerate(requested_powers_w):
        requested = float(requested)

        if basis == "setpoint":
            setpoint = requested

            if calibration is None:
                expected_actual = setpoint * t
            else:
                expected_actual = calibration.expected_power(setpoint, transmission=t)

        elif basis == "expected_actual":
            if calibration is None:
                setpoint = requested / t
                expected_actual = setpoint * t
            else:
                setpoint = calibration.setpoint_for_expected_power(
                    requested,
                    transmission=t,
                )
                expected_actual = calibration.expected_power(setpoint, transmission=t)

        else:
            raise ValueError(f"Unknown basis: {basis!r}")

        unclipped_setpoint = setpoint

        if allow_clipping:
            setpoint = min(max(setpoint, laser_min), laser_max)

            if calibration is None:
                expected_actual = setpoint * t
            else:
                expected_actual = calibration.expected_power(setpoint, transmission=t)

        else:
            if setpoint < laser_min or setpoint > laser_max:
                raise ValueError(
                    f"Requested point {i} requires setpoint {unclipped_setpoint:.6e} W, "
                    f"outside laser range [{laser_min:.6e}, {laser_max:.6e}] W"
                )

        points.append(
            PowerScanPoint(
                index=i,
                requested_power_w=requested,
                requested_basis=basis,
                setpoint_w=float(setpoint),
                expected_actual_power_w=float(expected_actual),
                filter_state=filter_state,
            )
        )

    return points
