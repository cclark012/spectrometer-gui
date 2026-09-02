from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from core.laser_models import LaserCalibrationPoint, LaserChannelInfo, PowerScanPoint
from core.records import PowerSnapshot, SpectrumRecord
from core.settings import AcquisitionSettings, DeviceConfig
from core.time_utils import utc_now_iso
from core.timing import StepTimer
from io_utils.calibration_io import load_calibration_csv, save_calibration_csv
from panels.acquisition_panel import AcquisitionPanel
from panels.filter_wheels_panel import FilterWheelPanel
from panels.laser_panel import LaserPanel
from panels.scan_panel import ScanPanel
from planning.filter_planning import enumerate_filter_states, plan_min_filter_changes
from planning.power_scan import CalibrationCurve, ScanPlan


class ScanCoordinator(QObject):
    """Coordinates power scans, calibration scans, and manual filter changes.

    The coordinator lives in the GUI thread and emits hardware requests to the
    existing worker controllers. It owns all scan state so MainWindow only needs
    to route signals and pass completed spectra back.
    """

    laser_set_power_requested = Signal(str, int, float)
    laser_set_enabled_requested = Signal(str, int, bool)
    power_read_once_requested = Signal(str)
    spectrum_requested = Signal()
    autosave_requested = Signal(object)
    auto_wavelength_requested = Signal(object)
    status_requested = Signal(str, int)
    active_changed = Signal(bool, str)

    def __init__(
        self,
        *,
        parent: QWidget,
        config: DeviceConfig,
        scan_panel: ScanPanel,
        laser_panel: LaserPanel,
        acquisition_panel: AcquisitionPanel,
        filter_wheel_panel: FilterWheelPanel,
    ) -> None:
        super().__init__(parent)
        self.parent_widget = parent
        self.config = config
        self.scan_panel = scan_panel
        self.laser_panel = laser_panel
        self.acquisition_panel = acquisition_panel
        self.filter_wheel_panel = filter_wheel_panel
        self.timer = StepTimer("power_scan", enabled=False)

        self.current_laser_calibration: CalibrationCurve | None = None
        self.calibration_results: list[LaserCalibrationPoint] = []
        self.reset_runtime_state()

    def reset_runtime_state(self) -> None:
        self.power_scan_active = False
        self.power_scan_abort_requested = False
        self.power_scan_points: list[PowerScanPoint] = []
        self.power_scan_laser: LaserChannelInfo | None = None
        self.power_scan_point_index = 0
        self.power_scan_repeat_index = 0
        self.current_scan_filter_state: str | None = None

        self.calibration_active = False
        self.calibration_points: list[PowerScanPoint] = []
        self.calibration_laser: LaserChannelInfo | None = None
        self.calibration_index = 0
        self.calibration_read_index = 0
        self.calibration_readings_w: list[float] = []

    @property
    def active(self) -> bool:
        return bool(self.power_scan_active or self.calibration_active)

    def set_timing_enabled(self, enabled: bool) -> None:
        self.timer.enabled = bool(enabled)

    def apply_scan_metadata(self, settings: AcquisitionSettings) -> AcquisitionSettings:
        if not self.power_scan_active or self.power_scan_laser is None:
            return settings
        if self.power_scan_point_index >= len(self.power_scan_points):
            return settings

        point = self.power_scan_points[self.power_scan_point_index]
        laser = self.power_scan_laser
        settings.scan_active = True
        settings.scan_index = int(self.power_scan_point_index)
        settings.scan_count = int(len(self.power_scan_points))
        settings.scan_basis = str(point.requested_basis)
        settings.scan_spacing = str(self.scan_panel.spacing())
        settings.laser_port = str(laser.port)
        settings.laser_box_id = str(laser.box_id)
        settings.laser_channel = int(laser.channel)
        settings.laser_wavelength_nm = float(laser.wavelength_nm)
        settings.laser_setpoint_w = float(point.setpoint_w)
        settings.requested_power_w = float(point.requested_power_w)
        settings.expected_actual_power_w = float(point.expected_actual_power_w)
        settings.filter_state = str(point.filter_state)
        return settings

    def selected_laser_or_warn(self) -> LaserChannelInfo | None:
        laser = self.laser_panel.selected_laser()
        if laser is None:
            QMessageBox.information(
                self.parent_widget,
                "No laser selected",
                "Select a laser in the Lasers tab before previewing or running a scan.",
            )
        return laser

    def _status(self, message: str, timeout_ms: int = 10_000) -> None:
        self.status_requested.emit(str(message), int(timeout_ms))

    def _validate_calibration_identity(
        self,
        laser: LaserChannelInfo,
    ) -> str | None:
        """Reject calibration data that clearly belongs to another laser."""

        if not self.calibration_results:
            return (
                "The loaded calibration has no laser identity metadata. "
                "Verify that it belongs to the selected laser."
            )

        reference = self.calibration_results[0]
        if reference.box_id and laser.box_id and reference.box_id != laser.box_id:
            raise ValueError(
                "The loaded calibration belongs to a different laser box: "
                f"{reference.box_id!r} instead of {laser.box_id!r}."
            )
        if reference.channel >= 0 and reference.channel != laser.channel:
            raise ValueError(
                "The loaded calibration belongs to channel "
                f"{reference.channel}, not channel {laser.channel}."
            )
        if (
            math.isfinite(reference.wavelength_nm)
            and math.isfinite(laser.wavelength_nm)
            and abs(reference.wavelength_nm - laser.wavelength_nm) > 1.0
        ):
            raise ValueError(
                "The loaded calibration wavelength does not match the selected "
                f"laser ({reference.wavelength_nm:.1f} nm versus "
                f"{laser.wavelength_nm:.1f} nm)."
            )
        return None

    def _make_scan_plan(self, laser: LaserChannelInfo) -> ScanPlan:
        calibration = (
            self.current_laser_calibration
            if self.scan_panel.scan_basis() == "expected_actual"
            else None
        )
        warnings: list[str] = []
        if calibration is not None:
            metadata_warning = self._validate_calibration_identity(laser)
            if metadata_warning:
                warnings.append(metadata_warning)

        if not self.filter_wheel_panel.planner_enabled():
            plan = self.scan_panel.make_plan_for_laser(
                laser_min_setpoint_w=float(laser.min_setpoint_w),
                laser_max_setpoint_w=float(laser.max_setpoint_w),
                calibration=calibration,
            )
            return ScanPlan(
                points=plan.points,
                warnings=[*warnings, *plan.warnings],
            )

        states = enumerate_filter_states(self.filter_wheel_panel.filter_wheels())
        plan_steps = plan_min_filter_changes(
            target_powers_w=self.scan_panel.requested_powers_w(),
            states=states,
            laser_min_setpoint_w=float(laser.min_setpoint_w),
            laser_max_setpoint_w=float(laser.max_setpoint_w),
            calibration=calibration,
        )
        points = [
            PowerScanPoint(
                index=int(step.index),
                requested_power_w=float(step.target_power_w),
                requested_basis="expected_actual",
                setpoint_w=float(step.required_setpoint_w),
                expected_actual_power_w=float(step.expected_actual_power_w),
                filter_state=step.filter_state.label,
            )
            for step in plan_steps
        ]
        return ScanPlan(points=points, warnings=warnings)

    def _show_warnings(self, warnings: list[str], *, confirm: bool) -> bool:
        if not warnings:
            return True
        text = "\n".join(warnings[:25])
        if len(warnings) > 25:
            text += f"\n\n... {len(warnings) - 25} more warning(s)."
        if not confirm:
            QMessageBox.warning(self.parent_widget, "Scan plan warnings", text)
            return True
        result = QMessageBox.warning(
            self.parent_widget,
            "Scan plan warnings",
            text + "\n\nContinue with this scan plan?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

    def preview_power_scan(self) -> None:
        laser = self.selected_laser_or_warn()
        if laser is None:
            return
        try:
            plan = self._make_scan_plan(laser)
        except Exception as exc:
            QMessageBox.critical(
                self.parent_widget,
                "Power scan preview failed",
                str(exc),
            )
            return
        self.scan_panel.set_points(plan.points, plan.warnings)
        self._show_warnings(plan.warnings, confirm=False)
        self._status(
            f"Prepared {len(plan.points)} scan point(s) for "
            f"{laser.wavelength_nm:.1f} nm laser."
        )

    def start_power_scan(self) -> bool:
        if self.active:
            return False
        laser = self.selected_laser_or_warn()
        if laser is None:
            return False
        try:
            plan = self._make_scan_plan(laser)
        except Exception as exc:
            QMessageBox.critical(
                self.parent_widget,
                "Power scan planning failed",
                str(exc),
            )
            return False
        self.scan_panel.set_points(plan.points, plan.warnings)
        if not self._show_warnings(plan.warnings, confirm=True):
            return False
        if not plan.points:
            QMessageBox.information(
                self.parent_widget,
                "No scan points",
                "No scan points were generated.",
            )
            return False

        self.timer.reset("power_scan")
        self.timer.log("start power scan")
        self.power_scan_active = True
        self.power_scan_abort_requested = False
        self.power_scan_points = list(plan.points)
        self.power_scan_laser = laser
        self.power_scan_point_index = 0
        self.power_scan_repeat_index = 0
        self.current_scan_filter_state = None
        self.scan_panel.set_running(True)
        self.active_changed.emit(True, "power_scan")
        self.acquisition_panel.set_live_enabled(False)
        self._status("Starting power scan...")
        self.auto_wavelength_requested.emit(laser)

        if self.scan_panel.should_enable_before_scan():
            self.laser_set_enabled_requested.emit(laser.port, laser.channel, True)
        else:
            self._start_next_power_scan_point()
        return True

    def abort_power_scan(self) -> None:
        """Abort whichever scan workflow currently owns the Scan panel."""

        if self.calibration_active:
            self._finish_calibration_scan("Calibration aborted.", disable_laser=True)
            return
        if not self.power_scan_active:
            return
        self.power_scan_abort_requested = True
        self._status("Power scan abort requested.")

    def fail_for_instrument_disconnect(
        self,
        message: str,
        *,
        disable_laser: bool,
    ) -> None:
        """Stop immediately when an awaited hardware callback cannot arrive."""

        if self.power_scan_active:
            self._finish_power_scan(
                f"Power scan stopped: {message}",
                disable_laser=disable_laser,
            )
        if self.calibration_active:
            self._finish_calibration_scan(
                f"Calibration stopped: {message}",
                disable_laser=disable_laser,
            )

    def _ensure_filter_state(self, point: PowerScanPoint) -> bool:
        state = str(point.filter_state or "none")
        if state == "none" or state == self.current_scan_filter_state:
            self.current_scan_filter_state = state
            return True
        result = QMessageBox.information(
            self.parent_widget,
            "Set neutral-density filters",
            f"Set the manual filter wheels to:\n\n{state}\n\nClick OK to continue.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if result != QMessageBox.StandardButton.Ok:
            return False
        self.current_scan_filter_state = state
        return True

    def _start_next_power_scan_point(self) -> None:
        if not self.power_scan_active:
            return
        if self.power_scan_abort_requested:
            self._finish_power_scan("Power scan aborted.", disable_laser=True)
            return
        if self.power_scan_point_index >= len(self.power_scan_points):
            if self.scan_panel.should_disable_after_scan() and self.power_scan_laser:
                self.laser_set_enabled_requested.emit(
                    self.power_scan_laser.port,
                    self.power_scan_laser.channel,
                    False,
                )
            else:
                self._finish_power_scan("Power scan complete.")
            return

        point = self.power_scan_points[self.power_scan_point_index]
        if not self._ensure_filter_state(point):
            self._finish_power_scan(
                "Power scan aborted during filter change.",
                disable_laser=True,
            )
            return

        laser = self.power_scan_laser
        if laser is None:
            self._finish_power_scan("Power scan failed: selected laser is unavailable.")
            return

        self.timer.log(
            f"set point {self.power_scan_point_index + 1}/"
            f"{len(self.power_scan_points)} to {point.setpoint_w:.6e} W"
        )
        self._status(
            f"Scan point {self.power_scan_point_index + 1}/"
            f"{len(self.power_scan_points)}, repeat "
            f"{self.power_scan_repeat_index + 1}/{self.scan_panel.repeats()}: "
            f"setting laser to {point.setpoint_w:.6e} W"
        )
        self.laser_set_power_requested.emit(
            laser.port,
            laser.channel,
            float(point.setpoint_w),
        )

    @Slot(str, int, float)
    def on_laser_power_set(self, port: str, channel: int, power_w: float) -> None:
        if self.calibration_active:
            laser = self.calibration_laser
            if laser and (port, int(channel)) == (laser.port, laser.channel):
                delay_ms = int(round(1000.0 * self.scan_panel.settling_seconds()))
                self.timer.log(f"calibration setpoint complete; settle {delay_ms} ms")
                QTimer.singleShot(delay_ms, self._read_current_calibration_power)
            return

        if not self.power_scan_active or self.power_scan_laser is None:
            return
        laser = self.power_scan_laser
        if (port, int(channel)) != (laser.port, laser.channel):
            return
        delay_ms = int(round(1000.0 * self.scan_panel.settling_seconds()))
        self.timer.log(f"laser setpoint complete; settle {delay_ms} ms")
        self._status(
            f"Laser set to {float(power_w):.6e} W. "
            f"Settling for {delay_ms / 1000.0:.2f} s."
        )
        QTimer.singleShot(delay_ms, self._acquire_current_power_scan_point)

    @staticmethod
    def _operation_error_summary(message: str) -> str:
        lines = [line.strip() for line in str(message).splitlines() if line.strip()]
        return lines[-1] if lines else "unknown laser-controller error"

    @Slot(str, int, str)
    def on_laser_power_set_failed(
        self,
        port: str,
        channel: int,
        message: str,
    ) -> None:
        summary = self._operation_error_summary(message)

        laser = self.calibration_laser if self.calibration_active else self.power_scan_laser
        if laser is None or (str(port), int(channel)) != (laser.port, laser.channel):
            return

        self.timer.log(f"laser setpoint failed: {summary}")
        if self.calibration_active:
            self._finish_calibration_scan(
                f"Calibration stopped: laser setpoint failed ({summary}).",
                disable_laser=True,
            )
        elif self.power_scan_active:
            self._finish_power_scan(
                f"Power scan stopped: laser setpoint failed ({summary}).",
                disable_laser=True,
            )

    @Slot(str, int, str)
    def on_laser_enabled_set_failed(
        self,
        port: str,
        channel: int,
        message: str,
    ) -> None:
        summary = self._operation_error_summary(message)

        laser = self.calibration_laser if self.calibration_active else self.power_scan_laser
        if laser is None or (str(port), int(channel)) != (laser.port, laser.channel):
            return

        self.timer.log(f"laser enable/disable failed: {summary}")
        if self.calibration_active:
            self._finish_calibration_scan(
                f"Calibration stopped: laser enable/disable failed ({summary}).",
                disable_laser=True,
            )
        elif self.power_scan_active:
            self._finish_power_scan(
                f"Power scan stopped: laser enable/disable failed ({summary}).",
                disable_laser=True,
            )

    @Slot(str, int, bool)
    def on_laser_enabled_set(self, port: str, channel: int, enabled: bool) -> None:
        if self.calibration_active:
            laser = self.calibration_laser
            if laser and (port, int(channel)) == (laser.port, laser.channel):
                if enabled:
                    self._start_next_calibration_point()
                else:
                    self._finish_calibration_scan("Calibration complete. Laser disabled.")
            return

        if not self.power_scan_active or self.power_scan_laser is None:
            return
        laser = self.power_scan_laser
        if (port, int(channel)) != (laser.port, laser.channel):
            return
        if enabled:
            self._start_next_power_scan_point()
        else:
            self._finish_power_scan("Power scan complete. Laser disabled.")

    def _acquire_current_power_scan_point(self) -> None:
        if not self.power_scan_active:
            return
        if self.power_scan_abort_requested:
            self._finish_power_scan("Power scan aborted.", disable_laser=True)
            return
        self.timer.log("settling complete; request spectrum")
        self.spectrum_requested.emit()

    def handle_spectrum_ready(self, record: SpectrumRecord) -> bool:
        if not self.power_scan_active:
            return False
        self.timer.log("spectrum ready")
        if self.scan_panel.should_autosave_scan_spectra():
            self.autosave_requested.emit(record)

        self.power_scan_repeat_index += 1
        if self.power_scan_repeat_index < self.scan_panel.repeats():
            # The setpoint and filter have not changed. Avoid redundant OBIS writes
            # and settling delays for repeated spectra at the same scan point.
            QTimer.singleShot(0, self._acquire_current_power_scan_point)
            return True

        self.power_scan_repeat_index = 0
        self.power_scan_point_index += 1
        QTimer.singleShot(0, self._start_next_power_scan_point)
        return True

    def handle_acquisition_failed(self, message: str) -> None:
        if self.power_scan_active:
            self._finish_power_scan(
                "Power scan stopped after acquisition failure.",
                disable_laser=True,
            )
        if self.calibration_active:
            self._finish_calibration_scan(
                "Calibration stopped after acquisition failure.",
                disable_laser=True,
            )
        self.timer.log(f"acquisition failed: {message.splitlines()[-1] if message else 'unknown'}")

    def _finish_power_scan(
        self,
        message: str,
        *,
        disable_laser: bool = False,
    ) -> None:
        if not self.power_scan_active:
            return
        laser = self.power_scan_laser
        self.power_scan_active = False
        self.power_scan_abort_requested = False
        self.power_scan_points = []
        self.power_scan_laser = None
        self.power_scan_point_index = 0
        self.power_scan_repeat_index = 0
        self.current_scan_filter_state = None
        self.scan_panel.set_running(False)
        self.active_changed.emit(False, "power_scan")
        if disable_laser and laser is not None:
            self.laser_set_enabled_requested.emit(laser.port, laser.channel, False)
        self.timer.log(message)
        self._status(message, 15_000)

    def start_calibration_scan(self) -> bool:
        if self.active:
            return False
        laser = self.selected_laser_or_warn()
        if laser is None:
            return False
        try:
            plan = self.scan_panel.make_calibration_plan_for_laser(
                laser_min_setpoint_w=float(laser.min_setpoint_w),
                laser_max_setpoint_w=float(laser.max_setpoint_w),
            )
        except Exception as exc:
            QMessageBox.critical(
                self.parent_widget,
                "Calibration preview failed",
                str(exc),
            )
            return False
        if not plan.points:
            return False
        self.scan_panel.set_points(plan.points, plan.warnings)
        if not self._show_warnings(plan.warnings, confirm=True):
            return False

        self.timer.reset("calibration")
        self.calibration_active = True
        self.calibration_points = list(plan.points)
        self.calibration_laser = laser
        self.calibration_index = 0
        self.calibration_read_index = 0
        self.calibration_readings_w = []
        self.calibration_results = []
        self.scan_panel.set_running(True)
        self.active_changed.emit(True, "calibration")
        self.acquisition_panel.set_live_enabled(False)
        self._status("Starting laser calibration scan...")
        self.auto_wavelength_requested.emit(laser)

        if self.scan_panel.should_enable_before_scan():
            self.laser_set_enabled_requested.emit(laser.port, laser.channel, True)
        else:
            self._start_next_calibration_point()
        return True

    def _start_next_calibration_point(self) -> None:
        if not self.calibration_active:
            return
        if self.calibration_index >= len(self.calibration_points):
            if not self._complete_calibration_curve():
                self._finish_calibration_scan(
                    "Calibration failed.",
                    disable_laser=True,
                )
                return
            if self.scan_panel.should_disable_after_scan() and self.calibration_laser:
                self.laser_set_enabled_requested.emit(
                    self.calibration_laser.port,
                    self.calibration_laser.channel,
                    False,
                )
            else:
                self._finish_calibration_scan("Calibration complete.")
            return

        point = self.calibration_points[self.calibration_index]
        laser = self.calibration_laser
        if laser is None:
            self._finish_calibration_scan("Calibration failed: laser unavailable.")
            return
        self.calibration_read_index = 0
        self.calibration_readings_w = []
        self._status(
            f"Calibration point {self.calibration_index + 1}/"
            f"{len(self.calibration_points)}: setting laser to "
            f"{point.setpoint_w:.6e} W"
        )
        self.laser_set_power_requested.emit(
            laser.port,
            laser.channel,
            float(point.setpoint_w),
        )

    def _read_current_calibration_power(self) -> None:
        if not self.calibration_active:
            return
        tag = f"calibration:{self.calibration_index}:{self.calibration_read_index}"
        self.power_read_once_requested.emit(tag)

    def _calibration_tag_is_current(self, tag: str) -> bool:
        try:
            prefix, index, read_index = str(tag).split(":", 2)
            return (
                prefix == "calibration"
                and int(index) == self.calibration_index
                and int(read_index) == self.calibration_read_index
            )
        except (TypeError, ValueError):
            return False

    @Slot(str, str)
    def on_power_read_failed(self, tag: str, message: str) -> None:
        if self.calibration_active and self._calibration_tag_is_current(tag):
            self._finish_calibration_scan(
                "Calibration stopped after a power-read failure.",
                disable_laser=True,
            )
            self.timer.log(
                f"power read failed: {message.splitlines()[-1] if message else 'unknown'}"
            )

    @Slot(str, object)
    def on_power_read_complete(self, tag: str, snapshot: PowerSnapshot) -> None:
        if not self.calibration_active or not self._calibration_tag_is_current(tag):
            return
        measured = self._calibration_power(snapshot)
        self.calibration_readings_w.append(measured)
        self.calibration_read_index += 1

        if self.calibration_read_index < self.scan_panel.repeats():
            QTimer.singleShot(0, self._read_current_calibration_power)
            return

        values = np.asarray(self.calibration_readings_w, dtype=float)
        values = values[np.isfinite(values)]
        point = self.calibration_points[self.calibration_index]
        laser = self.calibration_laser
        if laser is None:
            self._finish_calibration_scan("Calibration failed: laser unavailable.")
            return
        mean_w = float(np.mean(values)) if values.size else float("nan")
        std_w = float(np.std(values, ddof=0)) if values.size else float("nan")
        self.calibration_results.append(
            LaserCalibrationPoint(
                timestamp_utc=utc_now_iso(),
                port=laser.port,
                box_id=laser.box_id,
                channel=laser.channel,
                wavelength_nm=laser.wavelength_nm,
                setpoint_w=point.setpoint_w,
                measured_power_mean_w=mean_w,
                measured_power_std_w=std_w,
                n_reads=int(values.size),
                filter_state="none",
            )
        )
        self.calibration_index += 1
        QTimer.singleShot(0, self._start_next_calibration_point)

    def _calibration_power(self, snapshot: PowerSnapshot) -> float:
        if (
            self.config.power_meter_mode == "emulated"
            and self.config.laser_mode == "emulated"
            and self.calibration_active
            and self.calibration_index < len(self.calibration_points)
        ):
            setpoint = float(self.calibration_points[self.calibration_index].setpoint_w)
            return setpoint * 0.85 * (1.0 + 0.002 * math.sin(17.0 * setpoint))
        return float(snapshot.powers_w[0]) if snapshot.powers_w else float("nan")

    def _complete_calibration_curve(self) -> bool:
        setpoints = []
        measured = []
        for point in self.calibration_results:
            setpoint = float(point.setpoint_w)
            power = float(point.measured_power_mean_w)
            if math.isfinite(setpoint) and math.isfinite(power):
                setpoints.append(setpoint)
                measured.append(power)
        if len(setpoints) < 2:
            QMessageBox.warning(
                self.parent_widget,
                "Calibration failed",
                "Fewer than two valid calibration points were acquired.",
            )
            self.current_laser_calibration = None
            return False
        try:
            self.current_laser_calibration = CalibrationCurve(
                setpoint_w=np.asarray(setpoints, dtype=float),
                measured_power_w=np.asarray(measured, dtype=float),
                filter_state="none",
            )
            self._status(f"Calibration ready with {len(setpoints)} point(s).", 15_000)
            return True
        except Exception as exc:
            self.current_laser_calibration = None
            QMessageBox.warning(self.parent_widget, "Calibration invalid", str(exc))
            return False

    def _finish_calibration_scan(
        self,
        message: str,
        *,
        disable_laser: bool = False,
    ) -> None:
        if not self.calibration_active:
            return
        laser = self.calibration_laser
        self.calibration_active = False
        self.calibration_points = []
        self.calibration_laser = None
        self.calibration_index = 0
        self.calibration_read_index = 0
        self.calibration_readings_w = []
        self.scan_panel.set_running(False)
        self.active_changed.emit(False, "calibration")
        if disable_laser and laser is not None:
            self.laser_set_enabled_requested.emit(laser.port, laser.channel, False)
        self._status(message, 15_000)

    def save_current_calibration(self) -> None:
        if self.current_laser_calibration is None:
            QMessageBox.information(
                self.parent_widget,
                "No calibration",
                "No calibration curve is available.",
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self.parent_widget,
            "Save calibration",
            "laser_calibration.csv",
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        save_calibration_csv(
            Path(path),
            calibration=self.current_laser_calibration,
            points=self.calibration_results,
        )
        self._status(f"Saved calibration: {path}")

    def load_calibration(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self.parent_widget,
            "Load calibration",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        try:
            calibration, rows = load_calibration_csv(Path(path))
        except Exception as exc:
            QMessageBox.critical(
                self.parent_widget,
                "Load calibration failed",
                str(exc),
            )
            return
        self.current_laser_calibration = calibration
        self.calibration_results = rows
        self._status(
            f"Loaded calibration with {len(calibration.setpoint_w)} point(s)."
        )
