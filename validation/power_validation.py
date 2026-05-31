# power_validation.py

from __future__ import annotations

import math

from core.records import PowerSnapshot
from core.settings import PowerMonitorSettings


def power_snapshot_valid(
    snapshot: PowerSnapshot,
    settings: PowerMonitorSettings,
) -> tuple[bool, str]:
    if not settings.validation_enabled:
        return True, ""

    if not snapshot.powers_w:
        return False, "no power channels"

    for i, p in enumerate(snapshot.powers_w):
        p = float(p)

        if not math.isfinite(p):
            return False, f"ch{i + 1} non-finite power"

        if settings.reject_negative_power and p < 0.0:
            return False, f"ch{i + 1} negative power"

        if p > float(settings.max_valid_power_w):
            return (
                False,
                f"ch{i + 1} power {p:.6e} W exceeds "
                f"limit {settings.max_valid_power_w:.6e} W",
            )

    return True, ""
