from __future__ import annotations

import time
from collections import deque
from dataclasses import replace

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from core.gated_acquisition import (
    GatedAcquisitionSettings,
    GatedAction,
    GatedFrameMetadata,
    GatedPlan,
)
from core.laser_models import LaserChannelInfo
from core.records import SpectrumRecord
from core.settings import AcquisitionSettings
from planning.gated_sequence import build_gated_plan


class GatedAcquisitionCoordinator(QObject):
    """Software-timed laser/spectrum sequence state machine.

    Timing is measured from acknowledgement of the laser transition command. It is
    not a hardware-gated timing system; Windows scheduling, serial latency, camera
    readout, and queued Qt events contribute uncertainty. Requested and observed
    request delays are retained in frame metadata.
    """

    laser_set_enabled_requested = Signal(str, int, bool)
    spectrum_requested = Signal()
    autosave_requested = Signal(object)
    status_requested = Signal(str, int)
    active_changed = Signal(bool)
    plan_ready = Signal(object)
    completed = Signal(str)
    failed = Signal(str)
    aborted = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._abort_requested = False
        self._awaiting_laser = False
        self._awaiting_spectrum = False
        self._current_action: GatedAction | None = None
        self._actions: deque[GatedAction] = deque()
        self._settings = GatedAcquisitionSettings()
        self._laser: LaserChannelInfo | None = None
        self._pending_frame: GatedFrameMetadata | None = None
        self._transition_time_s: float | None = None
        self._sequence_id = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer_timeout)
        self._timer_callback = None

    @property
    def active(self) -> bool:
        return self._active

    def preview(self, settings: GatedAcquisitionSettings) -> GatedPlan:
        plan = build_gated_plan(settings)
        self.plan_ready.emit(plan)
        return plan

    def start(
        self,
        *,
        settings: GatedAcquisitionSettings,
        laser: LaserChannelInfo,
    ) -> GatedPlan:
        if self._active:
            raise RuntimeError("A gated acquisition is already active.")
        plan = build_gated_plan(settings)
        if plan.frame_count < 1:
            raise ValueError("The gated plan contains no spectrum frames.")

        self._settings = settings
        self._laser = laser
        self._actions = deque(plan.actions)
        self._abort_requested = False
        self._awaiting_laser = False
        self._awaiting_spectrum = False
        self._current_action = None
        self._pending_frame = None
        self._transition_time_s = None
        self._sequence_id = next(
            (
                action.frame.sequence_id
                for action in plan.actions
                if action.frame is not None
            ),
            "",
        )
        self._set_active(True)
        self.plan_ready.emit(plan)
        self.status_requested.emit(
            f"Gated acquisition started: {plan.frame_count} frame(s).",
            10_000,
        )
        QTimer.singleShot(0, self._advance)
        return plan

    @Slot()
    def abort(self) -> None:
        if not self._active:
            return
        self._abort_requested = True
        self._timer.stop()
        if not self._awaiting_laser and not self._awaiting_spectrum:
            self._finish_aborted()
        else:
            self.status_requested.emit(
                "Gated acquisition abort requested; waiting for the current "
                "instrument operation to finish.",
                10_000,
            )

    def apply_metadata(self, settings: AcquisitionSettings) -> AcquisitionSettings:
        if not self._active or self._pending_frame is None:
            return settings
        return replace(settings, gated=self._pending_frame)

    @Slot(str, int, bool)
    def on_laser_enabled_set(self, port: str, channel: int, enabled: bool) -> None:
        if not self._active or not self._awaiting_laser or self._laser is None:
            return
        if str(port) != str(self._laser.port) or int(channel) != int(self._laser.channel):
            return

        action = self._current_action
        self._awaiting_laser = False
        if action is not None and action.marks_transition:
            self._transition_time_s = time.perf_counter()

        if self._abort_requested:
            self._finish_aborted()
            return
        QTimer.singleShot(0, self._advance)

    @Slot(str, int, str)
    def on_laser_enabled_failed(self, port: str, channel: int, message: str) -> None:
        if not self._active or self._laser is None:
            return
        if str(port) == str(self._laser.port) and int(channel) == int(self._laser.channel):
            self._fail(f"Laser transition failed: {self._last_line(message)}")

    def handle_spectrum_ready(self, record: SpectrumRecord) -> bool:
        if not self._active or not self._awaiting_spectrum:
            return False

        self._awaiting_spectrum = False
        if self._settings.autosave_frames:
            self.autosave_requested.emit(record)
        self._pending_frame = None

        if self._abort_requested:
            self._finish_aborted()
        else:
            next_is_frame = bool(
                self._actions
                and self._actions[0].kind in {"acquire", "acquire_at_delay"}
            )
            self._schedule(
                (
                    max(0, int(self._settings.inter_frame_gap_ms))
                    if next_is_frame
                    else 0
                ),
                self._advance,
            )
        return True

    @Slot(str)
    def handle_acquisition_failed(self, message: str) -> None:
        if self._active and self._awaiting_spectrum:
            self._fail(f"Gated spectrum acquisition failed: {self._last_line(message)}")


    def _schedule(self, delay_ms: int, callback) -> None:
        self._timer.stop()
        self._timer_callback = callback
        self._timer.start(max(0, int(delay_ms)))

    @Slot()
    def _on_timer_timeout(self) -> None:
        callback = self._timer_callback
        self._timer_callback = None
        if callable(callback):
            callback()

    def _advance(self) -> None:
        if not self._active:
            return
        if self._abort_requested:
            self._finish_aborted()
            return
        if self._awaiting_laser or self._awaiting_spectrum:
            return
        if not self._actions:
            self._finish_complete()
            return

        action = self._actions.popleft()
        self._current_action = action

        if action.kind == "set_laser":
            if self._laser is None or action.laser_enabled is None:
                self._fail("The gated plan contains an invalid laser action.")
                return
            self._awaiting_laser = True
            self.laser_set_enabled_requested.emit(
                str(self._laser.port),
                int(self._laser.channel),
                bool(action.laser_enabled),
            )
            return

        if action.kind == "wait":
            self._schedule(max(0, int(action.wait_ms)), self._advance)
            return

        if action.kind == "acquire_at_delay":
            if self._transition_time_s is None:
                self._fail("A delayed frame was requested before a transition timestamp existed.")
                return
            elapsed_ms = 1000.0 * (time.perf_counter() - self._transition_time_s)
            remaining_ms = max(0, int(round(action.target_delay_ms - elapsed_ms)))
            if remaining_ms > 0:
                self._schedule(
                    remaining_ms,
                    lambda action=action: self._request_frame(action),
                )
                return
            self._request_frame(action)
            return

        if action.kind == "acquire":
            self._request_frame(action)
            return

        self._fail(f"Unknown gated action kind: {action.kind!r}")

    def _request_frame(self, action: GatedAction) -> None:
        if not self._active or action.frame is None:
            return
        elapsed_ms = float("nan")
        if self._transition_time_s is not None:
            elapsed_ms = 1000.0 * (time.perf_counter() - self._transition_time_s)

        self._pending_frame = replace(
            action.frame,
            request_elapsed_since_transition_ms=elapsed_ms,
        )
        self._awaiting_spectrum = True
        self.status_requested.emit(
            f"Gated frame {action.frame.frame_index + 1}/"
            f"{action.frame.frame_count}: {action.frame.label}",
            10_000,
        )
        self.spectrum_requested.emit()

    def _finish_complete(self) -> None:
        if self._settings.disable_after_finish and self._laser is not None:
            # Fire-and-forget final disable. The sequence is already complete, so
            # do not keep the coordinator active waiting for acknowledgement.
            self.laser_set_enabled_requested.emit(
                str(self._laser.port), int(self._laser.channel), False
            )
        sequence_id = self._sequence_id
        self._set_active(False)
        message = "Gated acquisition complete."
        self.status_requested.emit(message, 15_000)
        self.completed.emit(sequence_id)

    def _finish_aborted(self) -> None:
        if self._laser is not None:
            self.laser_set_enabled_requested.emit(
                str(self._laser.port), int(self._laser.channel), False
            )
        self._set_active(False)
        self.status_requested.emit("Gated acquisition aborted.", 10_000)
        self.aborted.emit()

    def _fail(self, message: str) -> None:
        if self._laser is not None:
            self.laser_set_enabled_requested.emit(
                str(self._laser.port), int(self._laser.channel), False
            )
        self._set_active(False)
        self.status_requested.emit(message, 15_000)
        self.failed.emit(message)

    def _set_active(self, active: bool) -> None:
        self._active = bool(active)
        if not active:
            self._timer.stop()
            self._timer_callback = None
            self._actions.clear()
            self._awaiting_laser = False
            self._awaiting_spectrum = False
            self._current_action = None
            self._pending_frame = None
            self._transition_time_s = None
            self._sequence_id = ""
            self._abort_requested = False
        self.active_changed.emit(self._active)

    @staticmethod
    def _last_line(message: str) -> str:
        lines = [line.strip() for line in str(message).splitlines() if line.strip()]
        return lines[-1] if lines else "unknown error"
