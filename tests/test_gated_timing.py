from __future__ import annotations

from core.gated_acquisition import GatedFrameMetadata
from processing.gated_timing import RobustTimingGuard


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

    decision = guard.evaluate(_frame(25.0, uncertainty_ms=4.0))

    assert decision.accepted


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
