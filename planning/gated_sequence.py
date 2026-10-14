from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from itertools import islice
import math
import uuid

from core.gated_acquisition import (
    GatedAcquisitionSettings,
    GatedAction,
    GatedFrameMetadata,
    GatedPlan,
    GatedPreviewRow,
)

MAX_GATED_FRAMES = 100_000
MAX_GATED_ACTIONS = 250_000
MAX_AVERAGED_TRACES = 5_000
GATED_MEMORY_WARNING_BYTES = 512 * 1024 * 1024

# Exact timing percentiles in the averaged-series accumulator retain a small
# group of Python floats per accepted frame. This conservative estimate also
# covers list references and per-frame power summaries.
_TIMING_SUMMARY_BYTES_PER_FRAME = 384


class LazyGatedActionSequence(Sequence[GatedAction]):
    """Repeatably generate a gated plan without retaining every action."""

    def __init__(
        self,
        factory: Callable[[], Iterator[GatedAction]],
        count: int,
    ) -> None:
        self._factory = factory
        self._count = max(0, int(count))

    def __len__(self) -> int:
        return self._count

    def __iter__(self) -> Iterator[GatedAction]:
        return self._factory()

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(self._count)
            return tuple(islice(iter(self), start, stop, step))

        item_index = int(index)
        if item_index < 0:
            item_index += self._count
        if item_index < 0 or item_index >= self._count:
            raise IndexError(index)
        return next(islice(iter(self), item_index, item_index + 1))


def _planned_cycles(settings: GatedAcquisitionSettings) -> int:
    cycles = int(settings.cycles)
    if settings.timing_guard_mode == "retain":
        return cycles * int(settings.timing_guard_attempt_multiplier)
    return cycles


def _estimated_plan_size(
    settings: GatedAcquisitionSettings,
    *,
    cycles: int | None = None,
) -> tuple[int, int]:
    cycles = int(_planned_cycles(settings) if cycles is None else cycles)
    on_actions = cycles if settings.enable_before_start else max(0, cycles - 1)
    repeat_gap_actions = (
        max(0, cycles - 1) if int(settings.inter_repeat_gap_ms) > 0 else 0
    )

    if settings.mode == "on_off_pair":
        frames = cycles * (
            int(settings.on_frames_per_cycle) + int(settings.off_frames_per_cycle)
        )
        actions = frames + on_actions + cycles
        actions += cycles if settings.on_settle_ms > 0 else 0
        actions += cycles if settings.off_settle_ms > 0 else 0
        return frames, actions + repeat_gap_actions

    if settings.mode == "delayed_after_off":
        frames = cycles * int(settings.delayed_frame_count)
        actions = frames + on_actions + cycles
        actions += cycles if settings.excitation_duration_ms > 0 else 0
        return frames, actions + repeat_gap_actions

    if settings.mode == "transition_series":
        frames = cycles * (
            int(settings.transition_pre_frames)
            + int(settings.transition_post_frames)
        )
        actions = frames + on_actions + cycles
        actions += cycles if settings.on_settle_ms > 0 else 0
        actions += cycles if settings.off_settle_ms > 0 else 0
        return frames, actions + repeat_gap_actions

    resolution_ms = int(settings.decay_resolution_ms)
    delay_count = (
        (int(settings.decay_stop_ms) - int(settings.decay_start_ms))
        // resolution_ms
        + 1
    )
    frames = cycles * delay_count
    phases = min(
        delay_count,
        int(settings.decay_burst_spacing_ms) // resolution_ms,
    )
    pump_cycles = cycles * phases
    on_actions = pump_cycles if settings.enable_before_start else max(0, pump_cycles - 1)
    actions = frames + on_actions + pump_cycles
    actions += pump_cycles if settings.excitation_duration_ms > 0 else 0
    return frames, actions + repeat_gap_actions


def _estimated_trace_count(settings: GatedAcquisitionSettings) -> int:
    if settings.mode == "on_off_pair":
        return int(settings.on_frames_per_cycle) + int(settings.off_frames_per_cycle)
    if settings.mode == "delayed_after_off":
        return int(settings.delayed_frame_count)
    if settings.mode == "transition_series":
        return int(settings.transition_pre_frames) + int(
            settings.transition_post_frames
        )
    return (
        (int(settings.decay_stop_ms) - int(settings.decay_start_ms))
        // int(settings.decay_resolution_ms)
        + 1
    )


def estimate_gated_peak_memory_bytes(
    settings: GatedAcquisitionSettings,
    *,
    spectrum_pixels: int,
) -> int:
    """Conservatively estimate in-process gated data retained at peak use."""

    pixels = max(1, int(spectrum_pixels or 1))
    # Only accepted frames are accumulated. In retain-until-valid mode the
    # rejected attempts are classified and released immediately.
    frames, _actions = _estimated_plan_size(
        settings,
        cycles=int(settings.cycles),
    )
    working_frame = 32 * pixels + 16_384
    if settings.output_mode != "averaged_series":
        return int(working_frame)

    traces = _estimated_trace_count(settings)
    # During finish(), group mean/M2 arrays coexist briefly with output mean/std
    # arrays. Four float64 arrays per trace is a conservative bound.
    spectral_arrays = 32 * pixels * max(1, traces)
    exact_timing_summaries = _TIMING_SUMMARY_BYTES_PER_FRAME * max(0, frames)
    wavelength_arrays = 16 * pixels
    return int(working_frame + spectral_arrays + exact_timing_summaries + wavelength_arrays)


def _preview_rows(settings: GatedAcquisitionSettings) -> tuple[GatedPreviewRow, ...]:
    rows: list[GatedPreviewRow] = []

    def add(
        action: str,
        *,
        state: str = "",
        timing: str = "",
        spectra: str = "",
        details: str = "",
    ) -> None:
        rows.append(GatedPreviewRow(action, state, timing, spectra, details))

    if settings.mode == "on_off_pair":
        if settings.enable_before_start:
            add("Laser ON", state="ON")
        if settings.on_settle_ms:
            add("Settle", state="ON", timing=f"{settings.on_settle_ms} ms")
        if settings.on_frames_per_cycle:
            add(
                "Acquire",
                state="ON",
                spectra=f"{settings.on_frames_per_cycle} per repeat",
            )
        add("Laser OFF", state="OFF", details="Start clock")
        if settings.off_settle_ms:
            add("Settle", state="OFF", timing=f"{settings.off_settle_ms} ms")
        if settings.off_frames_per_cycle:
            add(
                "Acquire",
                state="OFF",
                spectra=f"{settings.off_frames_per_cycle} per repeat",
            )
    elif settings.mode == "delayed_after_off":
        if settings.enable_before_start:
            add("Laser ON", state="ON")
        if settings.excitation_duration_ms:
            add("Pump", state="ON", timing=f"{settings.excitation_duration_ms} ms")
        add("Laser OFF", state="OFF", details="Start clock")
        last_delay = int(settings.delayed_start_ms) + (
            max(0, int(settings.delayed_frame_count) - 1)
            * int(settings.delayed_step_ms)
        )
        delay_detail = (
            f"at {settings.delayed_start_ms} ms"
            if int(settings.delayed_frame_count) == 1
            else (
                f"{settings.delayed_start_ms} to {last_delay} ms; "
                f"step {settings.delayed_step_ms} ms"
            )
        )
        add(
            "Acquire",
            state="OFF",
            spectra=f"{settings.delayed_frame_count} per repeat",
            details=delay_detail,
        )
    elif settings.mode == "transition_series":
        if settings.enable_before_start:
            add("Laser ON", state="ON")
        if settings.on_settle_ms:
            add("Settle", state="ON", timing=f"{settings.on_settle_ms} ms")
        if settings.transition_pre_frames:
            add(
                "Acquire",
                state="ON",
                spectra=f"{settings.transition_pre_frames} per repeat",
            )
        add("Laser OFF", state="OFF", details="Start clock")
        if settings.off_settle_ms:
            add("Settle", state="OFF", timing=f"{settings.off_settle_ms} ms")
        if settings.transition_post_frames:
            add(
                "Acquire",
                state="OFF",
                spectra=f"{settings.transition_post_frames} per repeat",
            )
    else:
        delay_count = _estimated_trace_count(settings)
        phase_count = min(
            delay_count,
            int(settings.decay_burst_spacing_ms) // int(settings.decay_resolution_ms),
        )
        if settings.enable_before_start:
            add("Laser ON", state="ON")
        if settings.excitation_duration_ms:
            add("Pump", state="ON", timing=f"{settings.excitation_duration_ms} ms")
        add("Laser OFF", state="OFF", details="Start burst clock")
        add(
            "Acquire",
            state="OFF",
            spectra=f"{delay_count} per curve",
            details=(
                f"{settings.decay_start_ms} to {settings.decay_stop_ms} ms; "
                f"{settings.decay_resolution_ms} ms grid; "
                f"{settings.decay_burst_spacing_ms} ms burst spacing; "
                f"{phase_count} pump phase(s)"
            ),
        )

    if settings.inter_frame_gap_ms:
        add(
            "Pause",
            timing=f"{settings.inter_frame_gap_ms} ms minimum",
            details="Between spectra",
        )
    if settings.inter_repeat_gap_ms and _planned_cycles(settings) > 1:
        add(
            "Pause",
            timing=f"{settings.inter_repeat_gap_ms} ms minimum",
            details="Between repeats",
        )
    if settings.cycles > 1 and settings.timing_guard_mode != "retain":
        add(
            "Repeat",
            spectra=f"x{settings.cycles - 1} more",
            details=f"{settings.cycles} total repeats",
        )
    if settings.timing_guard_mode == "retain":
        add(
            "Timing retention",
            spectra=f"{settings.cycles} valid per trace",
            details=(
                f"{settings.timing_guard_reference.replace('_', ' ')} "
                f"within ±{settings.timing_guard_tolerance_ms:g} ms; "
                f"at most {settings.timing_guard_attempt_multiplier}× attempts"
            ),
        )
    if settings.disable_after_finish:
        add("Finish", state="OFF")
    return tuple(rows)


def _iter_gated_actions(
    settings: GatedAcquisitionSettings,
    *,
    sequence_id: str,
    frame_count: int,
) -> Iterator[GatedAction]:
    action_index = 0
    frame_index = 0

    def action(
        kind: str,
        *,
        laser_enabled: bool | None = None,
        wait_ms: int = 0,
        target_delay_ms: int = 0,
        frame: GatedFrameMetadata | None = None,
        marks_transition: bool = False,
    ) -> GatedAction:
        nonlocal action_index
        result = GatedAction(
            index=action_index,
            kind=kind,
            laser_enabled=laser_enabled,
            wait_ms=int(wait_ms),
            target_delay_ms=int(target_delay_ms),
            frame=frame,
            marks_transition=bool(marks_transition),
        )
        action_index += 1
        return result

    def frame_action(
        *,
        cycle: int,
        label: str,
        state: str,
        target_delay_ms: int | None = None,
        phase_index: int = -1,
        repeat_index: int | None = None,
    ) -> GatedAction:
        nonlocal frame_index
        metadata = GatedFrameMetadata(
            sequence_id=sequence_id,
            mode=settings.mode,
            frame_index=frame_index,
            frame_count=frame_count,
            cycle_index=int(cycle),
            label=str(label),
            laser_state=str(state),
            requested_delay_ms=int(target_delay_ms or 0),
            phase_index=int(phase_index),
            repeat_index=int(cycle if repeat_index is None else repeat_index),
            repeat_count=int(settings.cycles),
            pump_duration_ms=(
                int(settings.excitation_duration_ms)
                if settings.mode in {"delayed_after_off", "interleaved_decay"}
                else None
            ),
            inter_frame_gap_ms=(
                int(settings.inter_frame_gap_ms)
                if int(settings.inter_frame_gap_ms) > 0
                else None
            ),
            inter_repeat_gap_ms=(
                int(settings.inter_repeat_gap_ms)
                if int(settings.inter_repeat_gap_ms) > 0
                else None
            ),
        )
        frame_index += 1
        return action(
            "acquire_at_delay" if target_delay_ms is not None else "acquire",
            target_delay_ms=int(target_delay_ms or 0),
            frame=metadata,
        )

    def set_laser(enabled: bool, *, transition: bool = False) -> GatedAction:
        return action(
            "set_laser",
            laser_enabled=bool(enabled),
            marks_transition=bool(transition),
        )

    def wait(milliseconds: int) -> GatedAction | None:
        return action("wait", wait_ms=int(milliseconds)) if int(milliseconds) > 0 else None

    planned_cycles = _planned_cycles(settings)

    if settings.mode == "on_off_pair":
        for cycle in range(planned_cycles):
            if cycle > 0 or settings.enable_before_start:
                yield set_laser(True)
            pending_wait = wait(settings.on_settle_ms)
            if pending_wait is not None:
                yield pending_wait
            for index in range(settings.on_frames_per_cycle):
                yield frame_action(cycle=cycle, label=f"on_{index + 1}", state="on")
            yield set_laser(False, transition=True)
            pending_wait = wait(settings.off_settle_ms)
            if pending_wait is not None:
                yield pending_wait
            for index in range(settings.off_frames_per_cycle):
                yield frame_action(cycle=cycle, label=f"off_{index + 1}", state="off")
            if cycle < planned_cycles - 1:
                pending_wait = wait(settings.inter_repeat_gap_ms)
                if pending_wait is not None:
                    yield pending_wait
        return

    if settings.mode == "delayed_after_off":
        for cycle in range(planned_cycles):
            if cycle > 0 or settings.enable_before_start:
                yield set_laser(True)
            pending_wait = wait(settings.excitation_duration_ms)
            if pending_wait is not None:
                yield pending_wait
            yield set_laser(False, transition=True)
            for index in range(settings.delayed_frame_count):
                target = settings.delayed_start_ms + index * settings.delayed_step_ms
                yield frame_action(
                    cycle=cycle,
                    label=f"delay_{target}_ms",
                    state="off",
                    target_delay_ms=target,
                )
            if cycle < planned_cycles - 1:
                pending_wait = wait(settings.inter_repeat_gap_ms)
                if pending_wait is not None:
                    yield pending_wait
        return

    if settings.mode == "transition_series":
        for cycle in range(planned_cycles):
            if cycle > 0 or settings.enable_before_start:
                yield set_laser(True)
            pending_wait = wait(settings.on_settle_ms)
            if pending_wait is not None:
                yield pending_wait
            for index in range(settings.transition_pre_frames):
                yield frame_action(
                    cycle=cycle,
                    label=f"pre_off_{index + 1}",
                    state="on",
                )
            yield set_laser(False, transition=True)
            pending_wait = wait(settings.off_settle_ms)
            if pending_wait is not None:
                yield pending_wait
            for index in range(settings.transition_post_frames):
                yield frame_action(
                    cycle=cycle,
                    label=f"post_off_{index + 1}",
                    state="off",
                )
            if cycle < planned_cycles - 1:
                pending_wait = wait(settings.inter_repeat_gap_ms)
                if pending_wait is not None:
                    yield pending_wait
        return

    resolution_ms = int(settings.decay_resolution_ms)
    burst_spacing_ms = int(settings.decay_burst_spacing_ms)
    phase_offsets = range(0, burst_spacing_ms, resolution_ms)
    pump_cycle = 0
    for repeat_index in range(planned_cycles):
        for phase_index, phase_ms in enumerate(phase_offsets):
            first_target_ms = int(settings.decay_start_ms) + int(phase_ms)
            if first_target_ms > int(settings.decay_stop_ms):
                continue
            if pump_cycle > 0 or settings.enable_before_start:
                yield set_laser(True)
            pending_wait = wait(settings.excitation_duration_ms)
            if pending_wait is not None:
                yield pending_wait
            yield set_laser(False, transition=True)
            for target_ms in range(
                first_target_ms,
                int(settings.decay_stop_ms) + 1,
                burst_spacing_ms,
            ):
                yield frame_action(
                    cycle=pump_cycle,
                    label=f"delay_{target_ms}_ms",
                    state="off",
                    target_delay_ms=target_ms,
                    phase_index=phase_index,
                    repeat_index=repeat_index,
                )
            pump_cycle += 1
        if repeat_index < planned_cycles - 1:
            pending_wait = wait(settings.inter_repeat_gap_ms)
            if pending_wait is not None:
                yield pending_wait


def build_gated_plan(
    settings: GatedAcquisitionSettings,
    *,
    spectrum_pixels: int = 0,
) -> GatedPlan:
    """Validate and create a deterministic, lazily expanded gated plan."""

    settings.validate()
    target_frames, _target_actions = _estimated_plan_size(
        settings,
        cycles=int(settings.cycles),
    )
    estimated_frames, estimated_actions = _estimated_plan_size(settings)
    if estimated_frames > MAX_GATED_FRAMES:
        raise ValueError(
            f"The gated plan would contain {estimated_frames:,} frames; the "
            f"safety limit is {MAX_GATED_FRAMES:,}. Reduce the delay range, "
            "repeats, or frames per cycle."
        )
    if estimated_actions > MAX_GATED_ACTIONS:
        raise ValueError(
            f"The gated plan would contain approximately {estimated_actions:,} "
            f"actions; the safety limit is {MAX_GATED_ACTIONS:,}."
        )
    estimated_traces = _estimated_trace_count(settings)
    if (
        settings.output_mode == "averaged_series"
        and estimated_traces > MAX_AVERAGED_TRACES
    ):
        raise ValueError(
            f"The averaged output would contain {estimated_traces:,} traces; "
            f"the matrix-file limit is {MAX_AVERAGED_TRACES:,}. Use a coarser "
            "delay grid or split the range."
        )

    warnings: list[str] = []
    if (
        settings.mode == "delayed_after_off"
        and settings.delayed_step_ms == 0
        and settings.delayed_frame_count > 1
    ):
        warnings.append(
            "Multiple delayed frames have the same requested delay. They will be "
            "acquired sequentially, so only the first can begin at that delay."
        )

    if settings.timing_guard_mode == "retain":
        warnings.append(
            "The plan stops as soon as every trace reaches its timing-valid "
            f"target. If it does not, it fails after at most {estimated_frames:,} "
            "detector attempts and saves any accepted partial average."
        )
        hint_ms = float(settings.frame_period_hint_ms)
        if (
            math.isfinite(hint_ms)
            and 2.0 * float(settings.timing_guard_tolerance_ms) < hint_ms
        ):
            warnings.append(
                f"The {2.0 * float(settings.timing_guard_tolerance_ms):g} ms "
                f"acceptance window is narrower than the estimated {hint_ms:.1f} "
                "ms detector period. Some pump cycles may have no eligible frame."
            )

    if settings.mode == "interleaved_decay":
        hint_ms = float(settings.frame_period_hint_ms)
        resolution_ms = int(settings.decay_resolution_ms)
        burst_spacing_ms = int(settings.decay_burst_spacing_ms)
        if math.isfinite(hint_ms) and hint_ms > burst_spacing_ms:
            warnings.append(
                f"The estimated {hint_ms:.1f} ms frame time exceeds the "
                f"{burst_spacing_ms} ms within-cycle spacing; later requests "
                "will miss their target delays."
            )
        if math.isfinite(hint_ms) and resolution_ms < hint_ms:
            warnings.append(
                f"The {resolution_ms} ms delay grid is finer than the estimated "
                f"{hint_ms:.1f} ms acquisition window. Interleaving samples the "
                "grid stroboscopically but does not create that temporal resolution."
            )
        if settings.output_mode == "averaged_series" and settings.cycles < 2:
            warnings.append(
                "Averaged-series output has one sample per delay because Repeats is 1."
            )

    estimated_memory = estimate_gated_peak_memory_bytes(
        settings,
        spectrum_pixels=spectrum_pixels,
    )
    if estimated_memory >= GATED_MEMORY_WARNING_BYTES:
        warnings.append(
            "Estimated peak gated memory is "
            f"{estimated_memory / (1024.0 * 1024.0):.0f} MiB, at or above the "
            "512 MiB review threshold."
        )

    sequence_id = uuid.uuid4().hex
    actions = LazyGatedActionSequence(
        lambda: _iter_gated_actions(
            settings,
            sequence_id=sequence_id,
            frame_count=estimated_frames,
        ),
        estimated_actions,
    )
    return GatedPlan(
        actions=actions,
        frame_count=estimated_frames,
        warnings=tuple(warnings),
        sequence_id=sequence_id,
        action_count=estimated_actions,
        preview_rows=_preview_rows(settings),
        estimated_peak_memory_bytes=estimated_memory,
        target_frame_count=target_frames,
        trace_count=estimated_traces,
    )
