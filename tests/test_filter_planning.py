import pytest

from core.filter_models import FilterPosition, FilterWheel
from planning.filter_planning import enumerate_filter_states, plan_min_filter_changes


def _states():
    wheels = [
        FilterWheel(
            name="W1",
            positions=(
                FilterPosition("open", 0.0),
                FilterPosition("OD1", 1.0),
                FilterPosition("OD2", 2.0),
            ),
        ),
        FilterWheel(
            name="W2",
            positions=(
                FilterPosition("open", 0.0),
                FilterPosition("OD1", 1.0),
            ),
        ),
    ]
    return enumerate_filter_states(wheels)


def test_enumerates_filter_combinations() -> None:
    states = _states()
    assert len(states) == 6
    assert states[0].optical_density == 0.0


def test_planner_finds_feasible_plan() -> None:
    steps = plan_min_filter_changes(
        target_powers_w=[1e-5, 1e-4, 1e-3],
        states=_states(),
        laser_min_setpoint_w=1e-3,
        laser_max_setpoint_w=1e-1,
    )
    assert len(steps) == 3
    assert all(1e-3 <= step.required_setpoint_w <= 1e-1 for step in steps)


def test_planner_rejects_unreachable_target() -> None:
    with pytest.raises(ValueError, match="No feasible filter state"):
        plan_min_filter_changes(
            target_powers_w=[1e3],
            states=_states(),
            laser_min_setpoint_w=1e-3,
            laser_max_setpoint_w=1e-1,
        )
