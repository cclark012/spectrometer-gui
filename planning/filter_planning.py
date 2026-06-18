from __future__ import annotations

import itertools
import math

import numpy as np

from core.filter_models import (  # pyright: ignore[reportMissingImports]
    FilterPlanStep,
    FilterState,
    FilterWheel,
)
from planning.power_scan import CalibrationCurve


def enumerate_filter_states(wheels: list[FilterWheel]) -> list[FilterState]:
    states = []

    for combo in itertools.product(*[wheel.positions for wheel in wheels]):
        od = sum(float(position.optical_density) for position in combo)
        transmission = 10.0 ** (-od)

        positions = tuple(
            (wheel.name, position.label)
            for wheel, position in zip(wheels, combo) # noqa
        )

        states.append(
            FilterState(
                positions=positions,
                optical_density=float(od),
                transmission=float(transmission),
            )
        )

    states.sort(key=lambda state: state.optical_density)
    return states


def setpoint_for_target(
    *,
    target_power_w: float,
    filter_state: FilterState,
    calibration: CalibrationCurve | None,
) -> float:
    t = float(filter_state.transmission)

    if not math.isfinite(t) or t <= 0:
        return float("nan")

    if calibration is None:
        return float(target_power_w) / t

    return calibration.setpoint_for_expected_power(
        float(target_power_w),
        transmission=t,
        allow_extrapolation=False,
    )


def expected_actual_power(
    *,
    setpoint_w: float,
    filter_state: FilterState,
    calibration: CalibrationCurve | None,
) -> float:
    if calibration is None:
        return float(setpoint_w) * float(filter_state.transmission)

    return calibration.expected_power(
        float(setpoint_w),
        transmission=float(filter_state.transmission),
    )


def feasible_state_indices(
    *,
    target_power_w: float,
    states: list[FilterState],
    laser_min_setpoint_w: float,
    laser_max_setpoint_w: float,
    calibration: CalibrationCurve | None,
) -> list[tuple[int, float, float]]:
    feasible = []

    for j, state in enumerate(states):
        setpoint = setpoint_for_target(
            target_power_w=float(target_power_w),
            filter_state=state,
            calibration=calibration,
        )

        if not math.isfinite(setpoint):
            continue

        if setpoint < float(laser_min_setpoint_w):
            continue

        if setpoint > float(laser_max_setpoint_w):
            continue

        expected = expected_actual_power(
            setpoint_w=setpoint,
            filter_state=state,
            calibration=calibration,
        )

        feasible.append((j, float(setpoint), float(expected)))

    return feasible


def plan_min_filter_changes(
    *,
    target_powers_w: list[float],
    states: list[FilterState],
    laser_min_setpoint_w: float,
    laser_max_setpoint_w: float,
    calibration: CalibrationCurve | None = None,
) -> list[FilterPlanStep]:
    n = len(target_powers_w)
    m = len(states)

    if n == 0:
        return []

    if m == 0:
        raise ValueError("No filter states available")

    feasible_by_point = []

    for target in target_powers_w:
        feasible = feasible_state_indices(
            target_power_w=float(target),
            states=states,
            laser_min_setpoint_w=float(laser_min_setpoint_w),
            laser_max_setpoint_w=float(laser_max_setpoint_w),
            calibration=calibration,
        )

        if not feasible:
            raise ValueError(f"No feasible filter state for target {target:.6e} W")

        feasible_by_point.append(feasible)

    inf = 10**12
    dp = np.full((n, m), inf, dtype=float)
    prev = np.full((n, m), -1, dtype=int)
    setpoint_table = np.full((n, m), np.nan, dtype=float)
    expected_table = np.full((n, m), np.nan, dtype=float)

    for j, setpoint, expected in feasible_by_point[0]:
        dp[0, j] = 0
        setpoint_table[0, j] = setpoint
        expected_table[0, j] = expected

    for i in range(1, n):
        feasible_indices = {j: (setpoint, expected) for j, setpoint, expected in feasible_by_point[i]} # noqa

        for j, (setpoint, expected) in feasible_indices.items():
            best_cost = inf
            best_prev = -1

            for k in range(m):
                if dp[i - 1, k] >= inf:
                    continue

                cost = dp[i - 1, k] + (0 if k == j else 1)

                if cost < best_cost:
                    best_cost = cost
                    best_prev = k

            dp[i, j] = best_cost
            prev[i, j] = best_prev
            setpoint_table[i, j] = setpoint
            expected_table[i, j] = expected

    last_state = int(np.argmin(dp[n - 1]))

    if dp[n - 1, last_state] >= inf:
        raise ValueError("No feasible filter plan found")

    state_indices = [last_state]

    for i in range(n - 1, 0, -1):
        state_indices.append(int(prev[i, state_indices[-1]]))

    state_indices.reverse()

    steps = []

    for i, j in enumerate(state_indices):
        steps.append(
            FilterPlanStep(
                index=i,
                target_power_w=float(target_powers_w[i]),
                filter_state=states[j],
                required_setpoint_w=float(setpoint_table[i, j]),
                expected_actual_power_w=float(expected_table[i, j]),
            )
        )

    return steps
