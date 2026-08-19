from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AutoAcquisitionResult:
    """Final state of one bounded automatic acquisition-tuning run."""

    success: bool
    message: str
    iterations: int
    integration_ms: int
    averages: int
    achieved_snr: float
    peak_fraction: float
    limit_reached: bool = False
