from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GatedMode = Literal[
    "on_off_pair",
    "delayed_after_off",
    "transition_series",
    "interleaved_decay",
]
GatedOutputMode = Literal["individual_frames", "averaged_series"]
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
    output_mode: GatedOutputMode = "individual_frames"

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
    phase_index: int = -1
    repeat_index: int = 0

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
class GatedPlan:
    actions: tuple[GatedAction, ...]
    frame_count: int
    warnings: tuple[str, ...] = ()
