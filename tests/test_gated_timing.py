from __future__ import annotations

from collections import deque
from dataclasses import replace

from core.gated_acquisition import GatedFrameMetadata
from processing.gated_timing import (
    FixedTimingWindowGuard,
    RetainedTraceGoal,
    RobustTimingGuard,
)


def _frame(observed_ms: float, *, requested_ms: int = 10, uncertainty_ms: float = 0.2):
    return GatedFrameMetadata(
        sequence_id="sequence",
        mode="delayed_after_off",
        frame_index=0,
        frame_count=1,
        cycle_index=0,
        label="delay",
        laser_state="off",
        requested_delay_ms=requested_ms,
        exposure_midpoint_estimate_elapsed_ms=observed_ms,
        exposure_timing_uncertainty_ms=uncertainty_ms,
    )


def test_robust_timing_guard_discards_large_outlier_after_warmup() -> None:
    guard = RobustTimingGuard(mode="discard", sigma=4.5, warmup=5)
    for observed in (11.0, 11.1, 10.9, 11.05, 10.95):
        assert guard.evaluate(_frame(observed)).accepted

    decision = guard.evaluate(_frame(30.0))

    assert not decision.accepted
    assert decision.quality == "outlier_discarded"
    assert guard.rejected_count == 1


def test_exposure_uncertainty_prevents_unrealistic_sub_ms_rejection() -> None:
    guard = RobustTimingGuard(mode="discard", sigma=4.5, warmup=5)
    for observed in (14.0, 14.1, 13.9, 14.05, 13.95):
        guard.evaluate(_frame(observed, uncertainty_ms=4.0))

    decision = guard.evaluate(_frame(17.5, uncertainty_ms=4.0))

    assert decision.accepted


def test_exposure_uncertainty_is_a_bound_not_a_sigma() -> None:
    guard = RobustTimingGuard(mode="discard", sigma=4.5, warmup=5)
    for observed in (14.0, 14.1, 13.9, 14.05, 13.95):
        guard.evaluate(_frame(observed, uncertainty_ms=4.0))

    decision = guard.evaluate(_frame(25.0, uncertainty_ms=4.0))

    assert not decision.accepted
    assert decision.threshold_ms == 4.0


def test_timing_guard_abort_threshold_is_enforced() -> None:
    guard = RobustTimingGuard(
        mode="discard",
        sigma=2.0,
        warmup=3,
        min_evaluated=5,
        max_rejected_fraction=0.2,
    )
    for observed in (10.0, 10.0, 10.0):
        guard.evaluate(_frame(observed, uncertainty_ms=0.05))
    guard.evaluate(_frame(30.0, uncertainty_ms=0.05))
    guard.evaluate(_frame(31.0, uncertainty_ms=0.05))

    assert guard.should_abort


def test_timing_baseline_has_bounded_memory_and_tracks_recent_frames() -> None:
    guard = RobustTimingGuard(
        mode="discard",
        sigma=20.0,
        warmup=3,
        baseline_window=16,
    )
    for index in range(1_000):
        guard.evaluate(_frame(10.0 + (index % 3) * 0.01))

    assert guard.evaluated_count == 1_000
    assert len(guard._accepted_residuals_ms) <= 16


def test_baselines_persist_by_backend_and_gated_type_across_runs() -> None:
    store: dict[str, deque[float]] = {}
    first = RobustTimingGuard(mode="discard", warmup=3, baseline_store=store)
    for observed in (11.0, 11.1, 10.9):
        first.evaluate(_frame(observed), baseline_key="qepro:delayed_after_off")

    second = RobustTimingGuard(mode="discard", warmup=3, baseline_store=store)
    decision = second.evaluate(
        _frame(30.0),
        baseline_key="qepro:delayed_after_off",
    )
    independent = second.evaluate(
        _frame(30.0),
        baseline_key="andor:delayed_after_off",
    )

    assert decision.quality == "outlier_discarded"
    assert independent.quality == "warmup"


def test_fixed_window_guard_can_use_exposure_start_or_midpoint() -> None:
    frame = replace(
        _frame(13.0, requested_ms=10, uncertainty_ms=6.0),
        exposure_window_start_elapsed_ms=9.0,
    )

    midpoint = FixedTimingWindowGuard(
        reference="exposure_midpoint",
        tolerance_ms=2.0,
    ).evaluate(frame)
    start = FixedTimingWindowGuard(
        reference="exposure_start",
        tolerance_ms=2.0,
    ).evaluate(frame)

    assert not midpoint.accepted
    assert midpoint.residual_ms == 3.0
    assert start.accepted
    assert start.residual_ms == -1.0


def test_fixed_window_guard_rejects_unavailable_timing() -> None:
    decision = FixedTimingWindowGuard(tolerance_ms=2.0).evaluate(
        _frame(float("nan"))
    )

    assert not decision.accepted
    assert decision.quality == "timing_unavailable_discarded"


def test_retained_trace_goal_tracks_each_delay_independently() -> None:
    goal = RetainedTraceGoal(target_per_trace=2, expected_trace_count=2)
    first = _frame(10.0, requested_ms=10)
    second = replace(first, label="delay_20_ms", requested_delay_ms=20)

    goal.accept(first)
    goal.accept(first)
    goal.accept(first)
    assert goal.is_satisfied(first)
    assert not goal.complete
    goal.accept(second)
    goal.accept(second)

    assert goal.complete
    assert goal.total_accepted == 4
