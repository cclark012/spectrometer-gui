from __future__ import annotations

import uuid

from core.gated_acquisition import (
    GatedAcquisitionSettings,
    GatedAction,
    GatedFrameMetadata,
    GatedPlan,
)


def build_gated_plan(settings: GatedAcquisitionSettings) -> GatedPlan:
    """Build a deterministic software-timed laser/spectrum action sequence."""

    settings.validate()
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
    ) -> None:
        spec = {
            "cycle": int(cycle),
            "label": str(label),
            "state": str(state),
            "target_delay_ms": int(target_delay_ms or 0),
            "kind": "acquire_at_delay" if target_delay_ms is not None else "acquire",
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
