from __future__ import annotations

"""Robust, exposure-aware quality control for software-timed gated frames."""

from dataclasses import dataclass
import math

import numpy as np

from core.gated_acquisition import GatedFrameMetadata


@dataclass(frozen=True, slots=True)
class TimingDecision:
    accepted: bool
    quality: str
    residual_ms: float = float("nan")
    center_ms: float = float("nan")
    robust_sigma_ms: float = float("nan")
    threshold_ms: float = float("nan")


def _robust_scale(values: list[float]) -> float:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size < 2:
        return float("nan")
    median = float(np.median(array))
    mad_sigma = 1.4826 * float(np.median(np.abs(array - median)))
    q25, q75 = np.percentile(array, [25.0, 75.0])
    iqr_sigma = float(q75 - q25) / 1.3489795003921634
    sample_sigma = float(np.std(array, ddof=1))
    candidates = [
        value
        for value in (mad_sigma, iqr_sigma, sample_sigma)
        if math.isfinite(value) and value > 0.0
    ]
    # Use the most conservative robust estimate to avoid discarding valid frames
    # when the warm-up sample is small or quantized by the Windows timer.
    return max(candidates) if candidates else 0.0


class RobustTimingGuard:
    """Classify timing residuals without requiring an arbitrary ms tolerance.

    The baseline is the median/MAD-like spread of previously accepted OFF
    frames.  The detector timing uncertainty is a lower bound on the rejection
    threshold, so a nominal 8 ms exposure is not judged against a sub-ms rule.
    """

    def __init__(
        self,
        *,
        mode: str = "discard",
        sigma: float = 4.5,
        warmup: int = 5,
        max_rejected_fraction: float = 0.25,
        min_evaluated: int = 10,
    ) -> None:
        self.mode = str(mode)
        self.sigma = float(sigma)
        self.warmup = max(3, int(warmup))
        self.max_rejected_fraction = float(max_rejected_fraction)
        self.min_evaluated = max(self.warmup, int(min_evaluated))
        self._accepted_residuals_ms: list[float] = []
        self.evaluated_count = 0
        self.rejected_count = 0

    @property
    def rejected_fraction(self) -> float:
        if self.evaluated_count < 1:
            return 0.0
        return self.rejected_count / self.evaluated_count

    @property
    def should_abort(self) -> bool:
        return (
            self.evaluated_count >= self.min_evaluated
            and self.rejected_fraction > self.max_rejected_fraction
        )

    def evaluate(self, frame: GatedFrameMetadata) -> TimingDecision:
        if self.mode == "off" or str(frame.laser_state).lower() != "off":
            return TimingDecision(True, "not_evaluated")

        observed_ms = float(frame.exposure_midpoint_estimate_elapsed_ms)
        requested_ms = float(frame.requested_delay_ms)
        if not math.isfinite(observed_ms) or not math.isfinite(requested_ms):
            return TimingDecision(True, "timing_unavailable")

        residual_ms = observed_ms - requested_ms
        self.evaluated_count += 1
        if len(self._accepted_residuals_ms) < self.warmup:
            self._accepted_residuals_ms.append(residual_ms)
            return TimingDecision(True, "warmup", residual_ms=residual_ms)

        center_ms = float(np.median(self._accepted_residuals_ms))
        robust_sigma_ms = _robust_scale(self._accepted_residuals_ms)
        uncertainty_ms = abs(float(frame.exposure_timing_uncertainty_ms))
        if not math.isfinite(uncertainty_ms):
            uncertainty_ms = 0.0
        # Timer quantization and perf_counter measurements below 50 us are not
        # a meaningful basis for a Windows software-gated rejection decision.
        scale_floor_ms = max(0.05, uncertainty_ms)
        threshold_ms = self.sigma * max(robust_sigma_ms, scale_floor_ms)
        outlier = abs(residual_ms - center_ms) > threshold_ms

        if outlier:
            self.rejected_count += 1
            return TimingDecision(
                self.mode != "discard",
                "outlier_flagged" if self.mode == "flag" else "outlier_discarded",
                residual_ms=residual_ms,
                center_ms=center_ms,
                robust_sigma_ms=robust_sigma_ms,
                threshold_ms=threshold_ms,
            )

        self._accepted_residuals_ms.append(residual_ms)
        return TimingDecision(
            True,
            "accepted",
            residual_ms=residual_ms,
            center_ms=center_ms,
            robust_sigma_ms=robust_sigma_ms,
            threshold_ms=threshold_ms,
        )
