from __future__ import annotations

import uuid

from core.gated_acquisition import (
    GatedAcquisitionSettings,
    GatedAction,
    GatedFrameMetadata,
    GatedPlan,
)

MAX_GATED_FRAMES = 100_000
MAX_GATED_ACTIONS = 250_000
MAX_AVERAGED_TRACES = 5_000


def _estimated_plan_size(
    settings: GatedAcquisitionSettings,
) -> tuple[int, int]:
    cycles = int(settings.cycles)
    on_actions = cycles if settings.enable_before_start else max(0, cycles - 1)

    if settings.mode == "on_off_pair":
        frames = cycles * (
            int(settings.on_frames_per_cycle) + int(settings.off_frames_per_cycle)
        )
        actions = frames + on_actions + cycles
        actions += cycles if settings.on_settle_ms > 0 else 0
        actions += cycles if settings.off_settle_ms > 0 else 0
        return frames, actions

    if settings.mode == "delayed_after_off":
        frames = cycles * int(settings.delayed_frame_count)
        actions = frames + on_actions + cycles
        actions += cycles if settings.excitation_duration_ms > 0 else 0
        return frames, actions

    if settings.mode == "transition_series":
        frames = cycles * (
            int(settings.transition_pre_frames)
            + int(settings.transition_post_frames)
        )
        actions = frames + on_actions + cycles
        actions += cycles if settings.on_settle_ms > 0 else 0
        actions += cycles if settings.off_settle_ms > 0 else 0
        return frames, actions

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
    return frames, actions


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


def build_gated_plan(settings: GatedAcquisitionSettings) -> GatedPlan:
    """Build a deterministic software-timed laser/spectrum action sequence."""

    settings.validate()
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
    sequence_id = uuid.uuid4().hex
    raw: list[dict] = []
    frame_specs: list[dict] = []
    warnings: list[str] = []

    def laser(enabled: bool, *, transition: bool = False) -> None:
        raw.append(
            {
                "kind": "set_laser",
                "laser_enabled": bool(enabled),
                "marks_transition": bool(transition),
            }
        )

    def wait(milliseconds: int) -> None:
        if int(milliseconds) > 0:
            raw.append({"kind": "wait", "wait_ms": int(milliseconds)})

    def frame(
        *,
        cycle: int,
        label: str,
        state: str,
        target_delay_ms: int | None = None,
        phase_index: int = -1,
        repeat_index: int | None = None,
    ) -> None:
        spec = {
            "cycle": int(cycle),
            "label": str(label),
            "state": str(state),
            "target_delay_ms": int(target_delay_ms or 0),
            "kind": "acquire_at_delay" if target_delay_ms is not None else "acquire",
            "phase_index": int(phase_index),
            "repeat_index": int(cycle if repeat_index is None else repeat_index),
        }
        frame_specs.append(spec)
        raw.append(spec)

    if settings.mode == "on_off_pair":
        for cycle in range(settings.cycles):
            if cycle > 0 or settings.enable_before_start:
                laser(True)
            wait(settings.on_settle_ms)
            for index in range(settings.on_frames_per_cycle):
                frame(cycle=cycle, label=f"on_{index + 1}", state="on")

            laser(False, transition=True)
            wait(settings.off_settle_ms)
            for index in range(settings.off_frames_per_cycle):
                frame(cycle=cycle, label=f"off_{index + 1}", state="off")

    elif settings.mode == "delayed_after_off":
        for cycle in range(settings.cycles):
            if cycle > 0 or settings.enable_before_start:
                laser(True)
            wait(settings.excitation_duration_ms)
            laser(False, transition=True)
            for index in range(settings.delayed_frame_count):
                target = settings.delayed_start_ms + index * settings.delayed_step_ms
                frame(
                    cycle=cycle,
                    label=f"delay_{target}_ms",
                    state="off",
                    target_delay_ms=target,
                )

        if settings.delayed_step_ms == 0 and settings.delayed_frame_count > 1:
            warnings.append(
                "Multiple delayed frames have the same requested delay. They will be "
                "acquired sequentially, so only the first can begin at that delay."
            )

    elif settings.mode == "transition_series":
        for cycle in range(settings.cycles):
            if cycle > 0 or settings.enable_before_start:
                laser(True)
            wait(settings.on_settle_ms)
            for index in range(settings.transition_pre_frames):
                frame(cycle=cycle, label=f"pre_off_{index + 1}", state="on")

            laser(False, transition=True)
            wait(settings.off_settle_ms)
            for index in range(settings.transition_post_frames):
                frame(cycle=cycle, label=f"post_off_{index + 1}", state="off")

    elif settings.mode == "interleaved_decay":
        resolution_ms = int(settings.decay_resolution_ms)
        burst_spacing_ms = int(settings.decay_burst_spacing_ms)
        phase_offsets = range(0, burst_spacing_ms, resolution_ms)
        pump_cycle = 0
        for repeat_index in range(settings.cycles):
            for phase_index, phase_ms in enumerate(phase_offsets):
                first_target_ms = int(settings.decay_start_ms) + int(phase_ms)
                if first_target_ms > int(settings.decay_stop_ms):
                    continue
                if pump_cycle > 0 or settings.enable_before_start:
                    laser(True)
                wait(settings.excitation_duration_ms)
                laser(False, transition=True)
                for target_ms in range(
                    first_target_ms,
                    int(settings.decay_stop_ms) + 1,
                    burst_spacing_ms,
                ):
                    frame(
                        cycle=pump_cycle,
                        label=f"delay_{target_ms}_ms",
                        state="off",
                        target_delay_ms=target_ms,
                        phase_index=phase_index,
                        repeat_index=repeat_index,
                    )
                pump_cycle += 1

        hint_ms = float(settings.frame_period_hint_ms)
        if hint_ms == hint_ms and hint_ms > burst_spacing_ms:
            warnings.append(
                f"The estimated {hint_ms:.1f} ms frame time exceeds the "
                f"{burst_spacing_ms} ms within-cycle spacing; later requests "
                "will miss their target delays."
            )
        if hint_ms == hint_ms and resolution_ms < hint_ms:
            warnings.append(
                f"The {resolution_ms} ms delay grid is finer than the estimated "
                f"{hint_ms:.1f} ms acquisition window. Interleaving samples the "
                "grid stroboscopically but does not create that temporal resolution."
            )
        if settings.output_mode == "averaged_series" and settings.cycles < 2:
            warnings.append(
                "Averaged-series output has one sample per delay because Repeats is 1."
            )

    frame_count = len(frame_specs)
    actions: list[GatedAction] = []
    frame_index = 0

    for index, item in enumerate(raw):
        metadata = None
        if item["kind"] in {"acquire", "acquire_at_delay"}:
            metadata = GatedFrameMetadata(
                sequence_id=sequence_id,
                mode=settings.mode,
                frame_index=frame_index,
                frame_count=frame_count,
                cycle_index=int(item["cycle"]),
                label=str(item["label"]),
                laser_state=str(item["state"]),
                requested_delay_ms=int(item["target_delay_ms"]),
                phase_index=int(item.get("phase_index", -1)),
                repeat_index=int(item.get("repeat_index", item["cycle"])),
            )
            frame_index += 1

        actions.append(
            GatedAction(
                index=index,
                kind=item["kind"],
                laser_enabled=item.get("laser_enabled"),
                wait_ms=int(item.get("wait_ms", 0)),
                target_delay_ms=int(item.get("target_delay_ms", 0)),
                frame=metadata,
                marks_transition=bool(item.get("marks_transition", False)),
            )
        )

    return GatedPlan(
        actions=tuple(actions),
        frame_count=frame_count,
        warnings=tuple(warnings),
    )
