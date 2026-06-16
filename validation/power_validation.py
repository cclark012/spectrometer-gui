from __future__ import annotations

import math

from core.records import PowerSnapshot
from core.settings import PowerMonitorSettings
from validation.power_status import newport_status_valid


def power_snapshot_valid(
    snapshot: PowerSnapshot,
    settings: PowerMonitorSettings,
) -> tuple[bool, str]:
    if not settings.validation_enabled:
        return True, ""

    if not snapshot.powers_w:
        return False, "no power channels"

    channels = tuple(settings.required_power_channels or (0,))

    for i in channels:
        if i < 0:
            return False, f"invalid required channel index {i}"

        if i >= len(snapshot.powers_w):
            return False, f"required channel {i + 1} missing from power reading"

        p = float(snapshot.powers_w[i])

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

        if settings.validate_status_words:
            if i >= len(snapshot.pm_status):
                return False, f"required channel {i + 1} missing status word"

            ok, reason = newport_status_valid(
                snapshot.pm_status[i],
                require_detector_present=bool(settings.require_detector_present),
                reject_range_changing=bool(settings.reject_range_changing),
                reject_saturated=bool(settings.reject_detector_saturated),
                reject_overrange=bool(settings.reject_overrange),
            )

            if not ok:
                return False, f"ch{i + 1} invalid Newport status: {reason}"

    return True, ""
