from __future__ import annotations

"""Robust, exposure-aware quality control for software-timed gated frames."""

import math
from collections import deque
from dataclasses import dataclass

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
        baseline_window: int = 256,
        baseline_store: dict[str, deque[float]] | None = None,
    ) -> None:
        self.mode = str(mode)
        self.sigma = float(sigma)
        self.warmup = max(3, int(warmup))
        self.max_rejected_fraction = float(max_rejected_fraction)
        self.min_evaluated = max(self.warmup, int(min_evaluated))
        # Recomputing robust statistics over every frame seen so far makes a
        # long gated run O(n^2).  A rolling baseline is both responsive to slow
        # scheduler drift and bounds each decision to constant work/memory.
        self.baseline_window = max(self.warmup, int(baseline_window))
        self._baseline_store = baseline_store if baseline_store is not None else {}
        self._accepted_residuals_ms = self._baseline_for("")
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

    def _baseline_for(self, key: str) -> deque[float]:
        normalized = str(key)
        baseline = self._baseline_store.get(normalized)
        if baseline is None or baseline.maxlen != self.baseline_window:
            retained = tuple(baseline or ())[-self.baseline_window :]
            baseline = deque(retained, maxlen=self.baseline_window)
            self._baseline_store[normalized] = baseline
        return baseline

    def evaluate(
        self,
        frame: GatedFrameMetadata,
        *,
        baseline_key: str = "",
    ) -> TimingDecision:
        if self.mode == "off" or str(frame.laser_state).lower() != "off":
            return TimingDecision(True, "not_evaluated")

        observed_ms = float(frame.exposure_midpoint_estimate_elapsed_ms)
        requested_ms = float(frame.requested_delay_ms)
        if not math.isfinite(observed_ms) or not math.isfinite(requested_ms):
            return TimingDecision(True, "timing_unavailable")

        residual_ms = observed_ms - requested_ms
        self.evaluated_count += 1
        baseline_values = self._baseline_for(baseline_key)
        # Preserve the historical attribute for diagnostics/tests that inspect
        # the active baseline directly.
        self._accepted_residuals_ms = baseline_values
        if len(baseline_values) < self.warmup:
            baseline_values.append(residual_ms)
            return TimingDecision(True, "warmup", residual_ms=residual_ms)

        baseline = list(baseline_values)
        center_ms = float(np.median(baseline))
        robust_sigma_ms = _robust_scale(baseline)
        uncertainty_ms = abs(float(frame.exposure_timing_uncertainty_ms))
        if not math.isfinite(uncertainty_ms):
            uncertainty_ms = 0.0
        # Timer quantization and perf_counter measurements below 50 us are not
        # a meaningful basis for a Windows software-gated rejection decision.
        # Exposure uncertainty is already a bound, rather than a standard
        # deviation, so do not multiply it by the configured sigma factor.
        statistical_threshold_ms = self.sigma * max(robust_sigma_ms, 0.05)
        threshold_ms = max(statistical_threshold_ms, uncertainty_ms)
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

        baseline_values.append(residual_ms)
        return TimingDecision(
            True,
            "accepted",
            residual_ms=residual_ms,
            center_ms=center_ms,
            robust_sigma_ms=robust_sigma_ms,
            threshold_ms=threshold_ms,
        )


class FixedTimingWindowGuard:
    """Keep frames whose selected exposure reference is inside a fixed window.

    Unlike :class:`RobustTimingGuard`, this policy has no warm-up baseline and
    never aborts on rejection fraction. The coordinator supplies a finite
    attempt ceiling and continues complete pump/OFF cycles until each trace has
    retained the requested number of valid spectra or that ceiling is reached.
    """

    def __init__(
        self,
        *,
        reference: str = "exposure_midpoint",
        tolerance_ms: float = 2.0,
    ) -> None:
        if reference not in {"exposure_midpoint", "exposure_start"}:
            raise ValueError(
                "reference must be exposure_midpoint or exposure_start"
            )
        if not math.isfinite(float(tolerance_ms)) or float(tolerance_ms) <= 0.0:
            raise ValueError("tolerance_ms must be positive and finite")
        self.mode = "retain"
        self.reference = str(reference)
        self.tolerance_ms = float(tolerance_ms)
        self.evaluated_count = 0
        self.rejected_count = 0

    @property
    def rejected_fraction(self) -> float:
        if self.evaluated_count < 1:
            return 0.0
        return self.rejected_count / self.evaluated_count

    @property
    def should_abort(self) -> bool:
        # The bounded plan, not rejection fraction, is the safety ceiling for
        # retain-until-valid measurements.
        return False

    def evaluate(
        self,
        frame: GatedFrameMetadata,
        *,
        baseline_key: str = "",
    ) -> TimingDecision:
        del baseline_key
        if str(frame.laser_state).lower() != "off":
            return TimingDecision(True, "not_evaluated")

        observed_ms = (
            float(frame.exposure_window_start_elapsed_ms)
            if self.reference == "exposure_start"
            else float(frame.exposure_midpoint_estimate_elapsed_ms)
        )
        requested_ms = float(frame.requested_delay_ms)
        self.evaluated_count += 1
        if not math.isfinite(observed_ms) or not math.isfinite(requested_ms):
            self.rejected_count += 1
            return TimingDecision(
                False,
                "timing_unavailable_discarded",
                threshold_ms=self.tolerance_ms,
            )

        residual_ms = observed_ms - requested_ms
        accepted = abs(residual_ms) <= self.tolerance_ms
        if not accepted:
            self.rejected_count += 1
        return TimingDecision(
            accepted,
            "within_fixed_window" if accepted else "outside_fixed_window_discarded",
            residual_ms=residual_ms,
            center_ms=0.0,
            robust_sigma_ms=float("nan"),
            threshold_ms=self.tolerance_ms,
        )


def gated_trace_key(frame: GatedFrameMetadata) -> tuple[str, str, int]:
    """Return the same stable trace identity used by the series accumulator."""

    return (
        str(frame.label),
        str(frame.laser_state),
        int(frame.requested_delay_ms),
    )


class RetainedTraceGoal:
    """Track a fixed accepted-sample target without retaining spectrum arrays."""

    def __init__(self, *, target_per_trace: int, expected_trace_count: int) -> None:
        self.target_per_trace = max(1, int(target_per_trace))
        self.expected_trace_count = max(1, int(expected_trace_count))
        self._counts: dict[tuple[str, str, int], int] = {}

    @property
    def total_accepted(self) -> int:
        return sum(self._counts.values())

    @property
    def total_target(self) -> int:
        return self.target_per_trace * self.expected_trace_count

    @property
    def complete(self) -> bool:
        return (
            len(self._counts) >= self.expected_trace_count
            and all(
                count >= self.target_per_trace
                for count in self._counts.values()
            )
        )

    def count(self, frame: GatedFrameMetadata) -> int:
        return self._counts.get(gated_trace_key(frame), 0)

    def is_satisfied(self, frame: GatedFrameMetadata) -> bool:
        return self.count(frame) >= self.target_per_trace

    def accept(self, frame: GatedFrameMetadata) -> None:
        key = gated_trace_key(frame)
        self._counts[key] = min(
            self.target_per_trace,
            self._counts.get(key, 0) + 1,
        )
