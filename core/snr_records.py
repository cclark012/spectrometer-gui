from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SNRMetrics:
    valid: bool
    message: str
    peak_snr: float = float("nan")
    integrated_snr: float = float("nan")
    noise_sigma_counts: float = float("nan")
    peak_signal_counts: float = float("nan")
    integrated_signal_counts_nm: float = float("nan")
    integrated_noise_counts_nm: float = float("nan")
    mean_signal_counts: float = float("nan")
    baseline_at_signal_center_counts: float = float("nan")
    peak_fraction_of_full_scale: float = float("nan")
    n_signal_pixels: int = 0
    n_noise_pixels: int = 0

    @classmethod
    def invalid(cls, message: str) -> "SNRMetrics": # noqa
        return cls(valid=False, message=str(message))


@dataclass(frozen=True, slots=True)
class AcquisitionSuggestion:
    integration_ms: int
    averages: int
    predicted_snr: float
    predicted_peak_fraction: float
    changed: bool
    limiting_reason: str
