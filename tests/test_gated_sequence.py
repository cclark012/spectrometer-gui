from __future__ import annotations

from itertools import islice

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


def test_inter_frame_gap_is_applied_by_coordinator_not_planner() -> None:
    plan = build_gated_plan(
        GatedAcquisitionSettings(
            mode="on_off_pair",
            cycles=1,
            on_settle_ms=0,
            off_settle_ms=0,
            inter_frame_gap_ms=17,
            on_frames_per_cycle=3,
            off_frames_per_cycle=2,
        )
    )

    assert not any(
        action.kind == "wait" and action.wait_ms == 17
        for action in plan.actions
    )


def test_interleaved_decay_fills_delay_grid_across_pump_cycles() -> None:
    plan = build_gated_plan(
        GatedAcquisitionSettings(
            mode="interleaved_decay",
            cycles=1,
            excitation_duration_ms=10,
            decay_start_ms=0,
            decay_stop_ms=10,
            decay_resolution_ms=2,
            decay_burst_spacing_ms=6,
            output_mode="averaged_series",
        )
    )

    frames = [action.frame for action in plan.actions if action.frame is not None]
    assert sorted(frame.requested_delay_ms for frame in frames) == [0, 2, 4, 6, 8, 10]
    assert {frame.phase_index for frame in frames} == {0, 1, 2}
    assert len(
        [
            action
            for action in plan.actions
            if action.kind == "set_laser" and action.marks_transition
        ]
    ) == 3


def test_interleaved_decay_rejects_nonintegral_phase_grid() -> None:
    settings = GatedAcquisitionSettings(
        mode="interleaved_decay",
        decay_resolution_ms=4,
        decay_burst_spacing_ms=10,
    )
    try:
        build_gated_plan(settings)
    except ValueError as exc:
        assert "integer multiple" in str(exc)
    else:
        raise AssertionError("Expected an invalid interleaved grid to be rejected")


def test_gated_plan_rejects_accidental_runaway_frame_count() -> None:
    settings = GatedAcquisitionSettings(
        mode="interleaved_decay",
        cycles=10,
        decay_start_ms=0,
        decay_stop_ms=100_000,
        decay_resolution_ms=1,
        decay_burst_spacing_ms=100,
    )

    try:
        build_gated_plan(settings)
    except ValueError as exc:
        assert "safety limit" in str(exc)
    else:
        raise AssertionError("Runaway gated plans must be rejected before allocation.")


def test_averaged_matrix_rejects_excessive_trace_width() -> None:
    settings = GatedAcquisitionSettings(
        mode="interleaved_decay",
        cycles=1,
        decay_start_ms=0,
        decay_stop_ms=6_000,
        decay_resolution_ms=1,
        decay_burst_spacing_ms=100,
        output_mode="averaged_series",
    )

    try:
        build_gated_plan(settings)
    except ValueError as exc:
        assert "matrix-file limit" in str(exc)
    else:
        raise AssertionError("Excessively wide gated series must be rejected.")


def test_large_plan_is_lazy_and_preview_is_compact() -> None:
    plan = build_gated_plan(
        GatedAcquisitionSettings(
            mode="delayed_after_off",
            cycles=1_000,
            excitation_duration_ms=0,
            delayed_frame_count=100,
            delayed_start_ms=0,
            delayed_step_ms=10,
        )
    )

    assert plan.frame_count == 100_000
    assert len(plan.actions) == plan.action_count
    assert not isinstance(plan.actions, (list, tuple))
    assert len(plan.preview_rows) < 10
    assert list(islice(plan.actions, 2)) == list(islice(plan.actions, 2))


def test_preflight_warns_when_averaged_retention_exceeds_512_mib() -> None:
    plan = build_gated_plan(
        GatedAcquisitionSettings(
            mode="interleaved_decay",
            cycles=1,
            excitation_duration_ms=0,
            decay_start_ms=0,
            decay_stop_ms=4_999,
            decay_resolution_ms=1,
            decay_burst_spacing_ms=100,
            output_mode="averaged_series",
        ),
        spectrum_pixels=4096,
    )

    assert plan.estimated_peak_memory_bytes >= 512 * 1024 * 1024
    assert any("512 MiB" in warning for warning in plan.warnings)


def test_frame_metadata_records_pump_and_gap_plan() -> None:
    settings = GatedAcquisitionSettings(
        mode="delayed_after_off",
        cycles=3,
        excitation_duration_ms=50,
        inter_frame_gap_ms=7,
        inter_repeat_gap_ms=125,
        delayed_frame_count=2,
        delayed_start_ms=0,
        delayed_step_ms=100,
    )

    plan = build_gated_plan(settings)
    actions = list(plan.actions)
    frames = [action.frame for action in actions if action.frame is not None]

    assert {frame.pump_duration_ms for frame in frames} == {50}
    assert {frame.inter_frame_gap_ms for frame in frames} == {7}
    assert {frame.inter_repeat_gap_ms for frame in frames} == {125}
    assert {frame.repeat_count for frame in frames} == {3}
    assert sum(action.kind == "wait" and action.wait_ms == 125 for action in actions) == 2


def test_retain_timing_plan_has_target_and_finite_attempt_ceiling() -> None:
    settings = GatedAcquisitionSettings(
        mode="delayed_after_off",
        cycles=100,
        delayed_frame_count=1,
        output_mode="averaged_series",
        timing_guard_mode="retain",
        timing_guard_reference="exposure_start",
        timing_guard_tolerance_ms=2.0,
        timing_guard_attempt_multiplier=3,
    )

    plan = build_gated_plan(settings)

    assert plan.target_frame_count == 100
    assert plan.frame_count == 300
    assert plan.trace_count == 1
    assert any("300 detector attempts" in warning for warning in plan.warnings)


def test_retain_timing_requires_averaged_delayed_workflow() -> None:
    for settings in (
        GatedAcquisitionSettings(
            mode="delayed_after_off",
            output_mode="individual_frames",
            timing_guard_mode="retain",
        ),
        GatedAcquisitionSettings(
            mode="on_off_pair",
            output_mode="averaged_series",
            timing_guard_mode="retain",
        ),
        GatedAcquisitionSettings(
            mode="delayed_after_off",
            output_mode="averaged_series",
            timing_guard_mode="retain",
            measure_power_per_frame=True,
        ),
    ):
        try:
            build_gated_plan(settings)
        except ValueError as exc:
            assert "Retain-until-valid" in str(exc)
        else:
            raise AssertionError("Invalid retain-until-valid workflow was accepted")


def test_retain_timing_rejects_duplicate_fixed_delay_traces() -> None:
    settings = GatedAcquisitionSettings(
        mode="delayed_after_off",
        cycles=10,
        delayed_frame_count=2,
        delayed_start_ms=10,
        delayed_step_ms=0,
        output_mode="averaged_series",
        timing_guard_mode="retain",
    )
    try:
        build_gated_plan(settings)
    except ValueError as exc:
        assert "distinct delayed traces" in str(exc)
    else:
        raise AssertionError("Duplicate retained trace identities were accepted")
