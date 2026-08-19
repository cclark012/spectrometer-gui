from __future__ import annotations

from core.gated_acquisition import GatedAcquisitionSettings
from planning.gated_sequence import build_gated_plan


def test_on_off_pair_frame_count() -> None:
    plan = build_gated_plan(
        GatedAcquisitionSettings(
            mode="on_off_pair",
            cycles=2,
            on_frames_per_cycle=2,
            off_frames_per_cycle=3,
        )
    )
    assert plan.frame_count == 10
    frames = [action.frame for action in plan.actions if action.frame is not None]
    assert [frame.frame_index for frame in frames] == list(range(10))
    assert {frame.laser_state for frame in frames} == {"on", "off"}


def test_delayed_frames_are_absolute_targets() -> None:
    plan = build_gated_plan(
        GatedAcquisitionSettings(
            mode="delayed_after_off",
            cycles=1,
            delayed_frame_count=4,
            delayed_start_ms=25,
            delayed_step_ms=50,
        )
    )
    delayed = [
        action for action in plan.actions if action.kind == "acquire_at_delay"
    ]
    assert [action.target_delay_ms for action in delayed] == [25, 75, 125, 175]


def test_transition_series_has_one_off_transition_per_cycle() -> None:
    plan = build_gated_plan(
        GatedAcquisitionSettings(
            mode="transition_series",
            cycles=3,
            transition_pre_frames=2,
            transition_post_frames=4,
        )
    )
    transitions = [
        action
        for action in plan.actions
        if action.kind == "set_laser" and action.marks_transition
    ]
    assert len(transitions) == 3
    assert plan.frame_count == 18
