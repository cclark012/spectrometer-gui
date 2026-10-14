from __future__ import annotations

from core.records import PowerSnapshot
from validation.power_channels import WrongPowerChannelTracker


def test_wrong_channel_warning_requires_configured_consecutive_count() -> None:
    tracker = WrongPowerChannelTracker()
    snapshot = PowerSnapshot([0.0, 2.0e-6], [0x118, 0x118])
    assert tracker.evaluate(
        snapshot,
        selected_channels=(0,),
        threshold_w=1.0e-6,
        consecutive=3,
    ) == ()
    assert tracker.evaluate(
        snapshot,
        selected_channels=(0,),
        threshold_w=1.0e-6,
        consecutive=3,
    ) == ()
    assert tracker.evaluate(
        snapshot,
        selected_channels=(0,),
        threshold_w=1.0e-6,
        consecutive=3,
    ) == (1,)
    assert tracker.evaluate(
        snapshot,
        selected_channels=(0,),
        threshold_w=1.0e-6,
        consecutive=3,
    ) == ()


def test_wrong_channel_warning_rearms_after_signal_clears_or_selection_changes() -> None:
    tracker = WrongPowerChannelTracker()
    high = PowerSnapshot([0.0, 3.0e-6], [0x118, 0x118])
    low = PowerSnapshot([0.0, 0.0], [0x118, 0x118])
    assert tracker.evaluate(
        high, selected_channels=(0,), threshold_w=1.0e-6, consecutive=1
    ) == (1,)
    assert tracker.evaluate(
        low, selected_channels=(0,), threshold_w=1.0e-6, consecutive=1
    ) == ()
    assert tracker.evaluate(
        high, selected_channels=(0,), threshold_w=1.0e-6, consecutive=1
    ) == (1,)
    assert tracker.evaluate(
        high, selected_channels=(1,), threshold_w=1.0e-6, consecutive=1
    ) == ()
