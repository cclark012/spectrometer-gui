from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import replace

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

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
from processing.gated_averaging import GatedSeriesAccumulator
from processing.gated_timing import RobustTimingGuard


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
    series_ready = Signal(object)
    autosave_series_requested = Signal(object)
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
        self._transition_time_ns: int | None = None
        self._sequence_id = ""
        self._accumulator: GatedSeriesAccumulator | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timer_timeout)
        self._timer_callback = None
        self._timer_deadline_ns: int | None = None
        self._timing_guard = RobustTimingGuard(mode="off")

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
        self._transition_time_ns = None
        self._sequence_id = next(
            (
                action.frame.sequence_id
                for action in plan.actions
                if action.frame is not None
            ),
            "",
        )
        self._accumulator = (
            GatedSeriesAccumulator()
            if settings.output_mode == "averaged_series"
            else None
        )
        self._timing_guard = RobustTimingGuard(
            mode=settings.timing_guard_mode,
            sigma=settings.timing_guard_sigma,
            warmup=settings.timing_guard_warmup,
            max_rejected_fraction=settings.timing_guard_max_rejected_fraction,
            min_evaluated=settings.timing_guard_min_evaluated,
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

    def fail_for_instrument_disconnect(
        self,
        message: str,
        *,
        disable_laser: bool,
    ) -> None:
        if not self._active:
            return
        self._fail(str(message), disable_laser=disable_laser)

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
            self._transition_time_ns = time.perf_counter_ns()
            self._transition_time_s = self._transition_time_ns * 1.0e-9

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
        if (
            record.gated is not None
            and record.gated.laser_state == "off"
            and self._transition_time_s is not None
        ):
            start_ms = (
                1000.0 * (record.acquisition_started_s - self._transition_time_s)
                if math.isfinite(record.acquisition_started_s)
                else float("nan")
            )
            end_ms = (
                1000.0 * (record.acquisition_finished_s - self._transition_time_s)
                if math.isfinite(record.acquisition_finished_s)
                else float("nan")
            )
            midpoint_ms = (
                0.5 * (start_ms + end_ms)
                if math.isfinite(start_ms) and math.isfinite(end_ms)
                else float("nan")
            )
            timing = record.acquisition_timing
            if timing is not None:
                exposure_start_ms = 1000.0 * (
                    timing.window_started_s - self._transition_time_s
                )
                exposure_end_ms = 1000.0 * (
                    timing.window_finished_s - self._transition_time_s
                )
                exposure_midpoint_ms = 1000.0 * (
                    timing.midpoint_estimate_s - self._transition_time_s
                )
                exposure_uncertainty_ms = 1000.0 * timing.uncertainty_s
                exposure_basis = str(timing.basis)
                exposure_sample_windows_ms = tuple(
                    (
                        1000.0 * (sample_start_s - self._transition_time_s),
                        1000.0 * (sample_end_s - self._transition_time_s),
                    )
                    for sample_start_s, sample_end_s in timing.sample_windows_s
                )
            else:
                exposure_start_ms = start_ms
                exposure_end_ms = end_ms
                exposure_midpoint_ms = midpoint_ms
                exposure_uncertainty_ms = (
                    0.5 * max(0.0, end_ms - start_ms)
                    if math.isfinite(start_ms) and math.isfinite(end_ms)
                    else float("nan")
                )
                exposure_basis = "controller_call_bounds"
                exposure_sample_windows_ms = ()
            record.gated = replace(
                record.gated,
                acquisition_call_start_elapsed_ms=start_ms,
                acquisition_call_midpoint_elapsed_ms=midpoint_ms,
                acquisition_call_end_elapsed_ms=end_ms,
                exposure_window_start_elapsed_ms=exposure_start_ms,
                exposure_window_end_elapsed_ms=exposure_end_ms,
                exposure_midpoint_estimate_elapsed_ms=exposure_midpoint_ms,
                exposure_timing_uncertainty_ms=exposure_uncertainty_ms,
                exposure_timing_basis=exposure_basis,
                exposure_sample_windows_elapsed_ms=exposure_sample_windows_ms,
            )

        decision = (
            self._timing_guard.evaluate(record.gated)
            if record.gated is not None
            else None
        )
        if decision is not None:
            record.gated = replace(
                record.gated,
                timing_error_ms=decision.residual_ms,
                timing_quality=decision.quality,
                timing_center_ms=decision.center_ms,
                timing_robust_sigma_ms=decision.robust_sigma_ms,
                timing_threshold_ms=decision.threshold_ms,
            )

        if self._timing_guard.should_abort:
            self._pending_frame = None
            self._fail(
                "Gated timing quality failed: "
                f"{self._timing_guard.rejected_count}/"
                f"{self._timing_guard.evaluated_count} evaluated frames were "
                "timing outliers. Increase the delay/spacing or reduce detector "
                "readout load before retrying."
            )
            return True

        accepted = decision is None or decision.accepted
        if not accepted:
            self.status_requested.emit(
                "Discarded a gated frame whose observed timing was a robust outlier.",
                10_000,
            )
        elif self._settings.output_mode == "averaged_series":
            try:
                if self._accumulator is None:
                    raise RuntimeError("The gated-series accumulator is unavailable.")
                self._accumulator.add(record)
            except Exception as exc:
                self._fail(f"Could not average gated frame: {exc}")
                return True
        elif self._settings.autosave_frames:
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
        deadline_ns = time.perf_counter_ns() + max(0, int(delay_ms)) * 1_000_000
        self._schedule_at(deadline_ns, callback)

    def _schedule_at(self, deadline_ns: int, callback) -> None:
        self._timer.stop()
        self._timer_callback = callback
        self._timer_deadline_ns = int(deadline_ns)
        self._arm_timer()

    def _arm_timer(self) -> None:
        if self._timer_callback is None or self._timer_deadline_ns is None:
            return
        remaining_ns = self._timer_deadline_ns - time.perf_counter_ns()
        if remaining_ns <= 0:
            self._timer.start(0)
            return
        # Ceiling prevents integer conversion from intentionally asking Qt to
        # wake before the absolute deadline; the timeout handler rechecks too.
        self._timer.start(max(1, int(math.ceil(remaining_ns / 1_000_000.0))))

    @Slot()
    def _on_timer_timeout(self) -> None:
        if (
            self._timer_deadline_ns is not None
            and time.perf_counter_ns() < self._timer_deadline_ns
        ):
            self._arm_timer()
            return
        callback = self._timer_callback
        self._timer_callback = None
        self._timer_deadline_ns = None
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
            if self._transition_time_ns is None:
                self._fail("The transition clock was unavailable for a delayed frame.")
                return
            deadline_ns = (
                self._transition_time_ns + int(action.target_delay_ms) * 1_000_000
            )
            self._schedule_at(
                deadline_ns,
                lambda action=action: self._request_frame(action),
            )
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
        series = None
        if self._settings.output_mode == "averaged_series":
            try:
                if self._accumulator is None:
                    raise RuntimeError("The gated-series accumulator is unavailable.")
                series = self._accumulator.finish()
                series = replace(
                    series,
                    timing_evaluated_count=self._timing_guard.evaluated_count,
                    timing_rejected_count=self._timing_guard.rejected_count,
                    timing_guard_method=self._settings.timing_guard_mode,
                )
            except Exception as exc:
                self._fail(f"Could not finalize averaged gated series: {exc}")
                return

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
        if series is not None:
            self.series_ready.emit(series)
            if self._settings.autosave_frames:
                self.autosave_series_requested.emit(series)
        self.completed.emit(sequence_id)

    def _finish_aborted(self) -> None:
        if self._laser is not None:
            self.laser_set_enabled_requested.emit(
                str(self._laser.port), int(self._laser.channel), False
            )
        self._set_active(False)
        self.status_requested.emit("Gated acquisition aborted.", 10_000)
        self.aborted.emit()

    def _fail(self, message: str, *, disable_laser: bool = True) -> None:
        if disable_laser and self._laser is not None:
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
            self._timer_deadline_ns = None
            self._actions.clear()
            self._awaiting_laser = False
            self._awaiting_spectrum = False
            self._current_action = None
            self._pending_frame = None
            self._transition_time_s = None
            self._transition_time_ns = None
            self._sequence_id = ""
            self._accumulator = None
            self._abort_requested = False
        self.active_changed.emit(self._active)

    @staticmethod
    def _last_line(message: str) -> str:
        lines = [line.strip() for line in str(message).splitlines() if line.strip()]
        return lines[-1] if lines else "unknown error"
