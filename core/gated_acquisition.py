from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

GatedMode = Literal[
    "on_off_pair",
    "delayed_after_off",
    "transition_series",
    "interleaved_decay",
]
GatedOutputMode = Literal["individual_frames", "averaged_series"]
GatedTimingGuardMode = Literal["auto", "off", "flag", "discard", "retain"]
GatedTimingReference = Literal["exposure_midpoint", "exposure_start"]
GatedActionKind = Literal[
    "set_laser",
    "wait",
    "acquire",
    "acquire_at_delay",
]


@dataclass(frozen=True, slots=True)
class GatedAcquisitionSettings:
    mode: GatedMode = "on_off_pair"
    cycles: int = 1

    on_settle_ms: int = 250
    off_settle_ms: int = 50
    excitation_duration_ms: int = 1000
    inter_frame_gap_ms: int = 0
    inter_repeat_gap_ms: int = 0

    on_frames_per_cycle: int = 1
    off_frames_per_cycle: int = 1

    delayed_frame_count: int = 10
    delayed_start_ms: int = 0
    delayed_step_ms: int = 100

    transition_pre_frames: int = 3
    transition_post_frames: int = 20

    decay_start_ms: int = 0
    decay_stop_ms: int = 1000
    decay_resolution_ms: int = 1
    decay_burst_spacing_ms: int = 100

    # Supplied by MainWindow from the current acquisition controls. It is a
    # planning hint, not a hardware guarantee or a persisted user setting.
    frame_period_hint_ms: float = float("nan")

    enable_before_start: bool = True
    disable_after_finish: bool = True
    autosave_frames: bool = True
    measure_power_per_frame: bool = False
    output_mode: GatedOutputMode = "individual_frames"
    timing_guard_mode: GatedTimingGuardMode = "auto"
    timing_guard_sigma: float = 4.5
    timing_guard_warmup: int = 5
    timing_guard_max_rejected_fraction: float = 0.25
    timing_guard_min_evaluated: int = 10
    # ``retain`` is an absolute, two-sided timing window intended for
    # low-signal averaged measurements. ``cycles`` becomes the number of
    # timing-valid spectra retained per trace; the attempt multiplier is a
    # finite safety ceiling rather than an open-ended retry loop.
    timing_guard_reference: GatedTimingReference = "exposure_midpoint"
    timing_guard_tolerance_ms: float = 2.0
    timing_guard_attempt_multiplier: int = 3

    def validate(self) -> None:
        if self.mode not in {
            "on_off_pair",
            "delayed_after_off",
            "transition_series",
            "interleaved_decay",
        }:
            raise ValueError(f"Unknown gated acquisition mode: {self.mode!r}")
        for name in (
            "cycles",
            "on_frames_per_cycle",
            "off_frames_per_cycle",
            "delayed_frame_count",
            "transition_pre_frames",
            "transition_post_frames",
            "decay_start_ms",
            "decay_stop_ms",
        ):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must not be negative")
        if int(self.cycles) < 1:
            raise ValueError("cycles must be at least 1")
        for name in (
            "on_settle_ms",
            "off_settle_ms",
            "excitation_duration_ms",
            "inter_frame_gap_ms",
            "inter_repeat_gap_ms",
            "delayed_start_ms",
            "delayed_step_ms",
        ):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must not be negative")
        if int(self.decay_stop_ms) < int(self.decay_start_ms):
            raise ValueError("decay_stop_ms must be at least decay_start_ms")
        if int(self.decay_resolution_ms) < 1:
            raise ValueError("decay_resolution_ms must be at least 1")
        if int(self.decay_burst_spacing_ms) < int(self.decay_resolution_ms):
            raise ValueError(
                "decay_burst_spacing_ms must be at least decay_resolution_ms"
            )
        if int(self.decay_burst_spacing_ms) % int(self.decay_resolution_ms):
            raise ValueError(
                "decay_burst_spacing_ms must be an integer multiple of "
                "decay_resolution_ms"
            )
        if self.output_mode not in {"individual_frames", "averaged_series"}:
            raise ValueError(f"Unknown gated output mode: {self.output_mode!r}")
        if self.timing_guard_mode not in {
            "auto",
            "off",
            "flag",
            "discard",
            "retain",
        }:
            raise ValueError(
                f"Unknown gated timing-guard mode: {self.timing_guard_mode!r}"
            )
        if not 2.0 <= float(self.timing_guard_sigma) <= 20.0:
            raise ValueError("timing_guard_sigma must be between 2 and 20")
        if int(self.timing_guard_warmup) < 3:
            raise ValueError("timing_guard_warmup must be at least 3")
        if not 0.0 <= float(self.timing_guard_max_rejected_fraction) < 1.0:
            raise ValueError(
                "timing_guard_max_rejected_fraction must be in [0, 1)"
            )
        if int(self.timing_guard_min_evaluated) < int(self.timing_guard_warmup):
            raise ValueError(
                "timing_guard_min_evaluated must be at least timing_guard_warmup"
            )
        if self.timing_guard_reference not in {
            "exposure_midpoint",
            "exposure_start",
        }:
            raise ValueError(
                "timing_guard_reference must be exposure_midpoint or exposure_start"
            )
        if not 0.0 < float(self.timing_guard_tolerance_ms) <= 86_400_000.0:
            raise ValueError("timing_guard_tolerance_ms must be positive")
        if not 1 <= int(self.timing_guard_attempt_multiplier) <= 100:
            raise ValueError(
                "timing_guard_attempt_multiplier must be between 1 and 100"
            )
        if self.timing_guard_mode == "retain":
            if self.output_mode != "averaged_series":
                raise ValueError(
                    "Retain-until-valid timing requires one averaged series file."
                )
            if self.mode not in {"delayed_after_off", "interleaved_decay"}:
                raise ValueError(
                    "Retain-until-valid timing is supported for fixed-delay and "
                    "interleaved-decay acquisitions."
                )
            if self.measure_power_per_frame:
                raise ValueError(
                    "Retain-until-valid timing cannot measure Newport power per "
                    "frame because that queued I/O precedes the detector call."
                )
            if (
                self.mode == "delayed_after_off"
                and int(self.delayed_frame_count) > 1
                and int(self.delayed_step_ms) == 0
            ):
                raise ValueError(
                    "Retain-until-valid timing requires distinct delayed traces. "
                    "Use one delayed frame when every spectrum has the same delay."
                )


@dataclass(frozen=True, slots=True)
class GatedFrameMetadata:
    sequence_id: str
    mode: str
    frame_index: int
    frame_count: int
    cycle_index: int
    label: str
    laser_state: str
    requested_delay_ms: int = 0
    request_elapsed_since_transition_ms: float = float("nan")
    acquisition_call_start_elapsed_ms: float = float("nan")
    acquisition_call_midpoint_elapsed_ms: float = float("nan")
    acquisition_call_end_elapsed_ms: float = float("nan")
    exposure_window_start_elapsed_ms: float = float("nan")
    exposure_window_end_elapsed_ms: float = float("nan")
    exposure_midpoint_estimate_elapsed_ms: float = float("nan")
    exposure_timing_uncertainty_ms: float = float("nan")
    exposure_timing_basis: str = ""
    transition_timing_basis: str = ""
    exposure_sample_windows_elapsed_ms: tuple[tuple[float, float], ...] = ()
    timing_error_ms: float = float("nan")
    timing_quality: str = "not_evaluated"
    timing_reference: str = "exposure_midpoint"
    timing_center_ms: float = float("nan")
    timing_robust_sigma_ms: float = float("nan")
    timing_threshold_ms: float = float("nan")
    phase_index: int = -1
    repeat_index: int = 0
    repeat_count: int = 1
    pump_duration_ms: int | None = None
    inter_frame_gap_ms: int | None = None
    inter_repeat_gap_ms: int | None = None

    @property
    def active(self) -> bool:
        return bool(self.sequence_id)


@dataclass(frozen=True, slots=True)
class GatedAction:
    index: int
    kind: GatedActionKind
    laser_enabled: bool | None = None
    wait_ms: int = 0
    target_delay_ms: int = 0
    frame: GatedFrameMetadata | None = None
    marks_transition: bool = False


@dataclass(frozen=True, slots=True)
class GatedPreviewRow:
    """One human-readable row in the compact, non-expanded plan preview."""

    action: str
    state: str = ""
    timing: str = ""
    spectra: str = ""
    details: str = ""


@dataclass(frozen=True, slots=True)
class GatedPlan:
    # ``actions`` may be a lazy Sequence.  Iteration must be repeatable so the
    # coordinator and diagnostics can each obtain an independent iterator.
    actions: Sequence[GatedAction]
    frame_count: int
    warnings: tuple[str, ...] = ()
    sequence_id: str = ""
    action_count: int = 0
    preview_rows: tuple[GatedPreviewRow, ...] = ()
    estimated_peak_memory_bytes: int = 0
    target_frame_count: int = 0
    trace_count: int = 0
