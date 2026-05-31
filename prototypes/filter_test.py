from core.filter_models import FilterPosition, FilterWheel
from planning.filter_planning import enumerate_filter_states, plan_min_filter_changes

wheel1 = FilterWheel(
    name="W1",
    positions=(
        FilterPosition("open", 0.0),
        FilterPosition("OD1", 1.0),
        FilterPosition("OD2", 2.0),
    ),
)

wheel2 = FilterWheel(
    name="W2",
    positions=(
        FilterPosition("open", 0.0),
        FilterPosition("OD1", 1.0),
        FilterPosition("OD2", 2.0),
    ),
)

states = enumerate_filter_states([wheel1, wheel2])

steps = plan_min_filter_changes(
    target_powers_w=[1e-6, 5e-6, 1e-4, 1e-3, 1e-2],
    states=states,
    laser_min_setpoint_w=5e-4,
    laser_max_setpoint_w=4e-2,
    calibration=None,
)

for step in steps:
    print(
        step.index,
        step.target_power_w,
        step.filter_state.label,
        step.required_setpoint_w,
        step.expected_actual_power_w,
    )
