from __future__ import annotations

import math
import sys
import time
from dataclasses import replace

import numpy as np
from PySide6.QtCore import Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
)

from controllers.file_io_controller import FileIOController
from controllers.instrument_runtime import InstrumentRuntime
from controllers.preferences_controller import PreferencesController
from controllers.scan_coordinator import ScanCoordinator
from core.laser_models import LaserChannelInfo
from core.performance import PerformanceMonitor, PerformanceSnapshot
from core.records import (
    BackgroundSpectrum,
    PowerSnapshot,
    PowerTracePoint,
    SpectrometerCapabilities,
    SpectrometerInfo,
    SpectrumRecord,
)
from core.restart import RESTART_EXIT_CODE
from core.settings import (
    AcquisitionSettings,
    DeviceConfig,
    DisplaySettings,
    FileNameSettings,
    PlotStyleSettings,
    PowerMonitorSettings,
    SignalWarningSettings,
    SNRSettings,
)
from core.time_utils import utc_now_iso
from core.units import format_power_w
from dialogs.display_settings_dialog import DisplaySettingsDialog
from dialogs.performance_settings_dialog import PerformanceSettingsDialog
from dialogs.power_details_dialog import PowerDetailsDialog
from dialogs.settings_dialog import AppSettingsDialog
from dialogs.snr_settings_dialog import SNRSettingsDialog
from dialogs.spectrometer_details_dialog import SpectrometerDetailsDialog
from dialogs.spectrum_axis_dialog import SpectrumAxisDialog
from panels.acquisition_panel import AcquisitionPanel
from panels.filter_wheels_panel import FilterWheelPanel
from panels.laser_panel import LaserPanel
from panels.main_window_actions import build_main_window_actions
from panels.monitor_panel import MonitorPanel
from panels.power_panel import PowerPanel
from panels.scan_panel import ScanPanel
from panels.spectrum_panel import SpectrumPanel


class MainWindow(QMainWindow):
    """Top-level UI shell and cross-controller coordinator.

    Device-specific behavior stays in worker controllers; scan/calibration state is
    delegated to ScanCoordinator; plot internals stay inside their panels.
    """

    acquire_requested = Signal(object)
    power_poll_requested = Signal()
    power_settings_changed = Signal(object)
    power_meter_wavelength_requested = Signal(int)
    power_read_once_requested = Signal(str)
    laser_set_power_requested = Signal(str, int, float)
    laser_set_enabled_requested = Signal(str, int, bool)
    background_capture_requested = Signal(object)
    background_clear_requested = Signal()
    tec_target_requested = Signal(float)
    tec_enabled_requested = Signal(bool)
    spectrometer_temperature_requested = Signal()
    spectrometer_capabilities_requested = Signal()

    def __init__(self, config: DeviceConfig) -> None:
        super().__init__()
        self.setWindowTitle("Magneto-PL Spectrum Acquisition")
        self.resize(1600, 850)

        self._application_exit_code = 0
        self.config = config
        self.file_name_settings = FileNameSettings()
        self.power_monitor_settings = PowerMonitorSettings()
        self.signal_warning_settings = SignalWarningSettings()
        self.plot_style_settings = PlotStyleSettings()
        self.display_settings = DisplaySettings()
        self.snr_settings = SNRSettings()
        self.spectrometer_info = SpectrometerInfo()
        self.spectrometer_capabilities: SpectrometerCapabilities | None = None

        self.app_t0 = time.perf_counter()
        self.current_record: SpectrumRecord | None = None
        self.acquiring = False
        self.auto_update_power_meter_wavelength = True
        self.last_power_meter_wavelength_nm: int | None = None
        self.last_signal_warning_s = -1.0e99

        self._build_central_views()
        self._build_left_dock()
        self._build_power_dock()
        self._build_status_bar()
        self._build_file_io_controller()
        self._build_actions()
        self._build_timers()
        self._build_scan_coordinator()
        self._build_preferences_controller()

        self.performance_monitor = PerformanceMonitor(
            enabled=self.display_settings.performance_enabled,
            rate_window_s=self.display_settings.performance_rate_window_s,
            report_interval_ms=self.display_settings.performance_report_interval_ms,
            probe_interval_ms=self.display_settings.event_loop_probe_interval_ms,
            parent=self,
        )
        self.spectrum_panel.redrawn.connect(
            self.performance_monitor.mark_spectrum_redraw
        )
        self.monitor_panel.redrawn.connect(
            self.performance_monitor.mark_monitor_redraw
        )
        self.power_panel.redrawn.connect(
            self.performance_monitor.mark_power_redraw
        )
        self.performance_monitor.updated.connect(self._on_performance_updated)

        self._load_preferences()
        self._apply_loaded_preferences()

        self._start_instrument_runtime()
        self._apply_snr_settings()
        self._apply_power_monitor_settings()

        restored = self._restore_window_layout()
        if not restored:
            QTimer.singleShot(0, self._apply_initial_layout)



    # ------------------------------------------------------------------ UI setup

    def _build_central_views(self) -> None:
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.spectrum_panel = SpectrumPanel(parent=self)
        self.monitor_panel = MonitorPanel(parent=self)
        self.monitor_panel.set_application_t0(self.app_t0)
        self.monitor_panel.cleared.connect(self._on_monitor_cleared)
        self.monitor_panel.memory_warning_requested.connect(
            self._on_monitor_memory_warning
        )

        self.tabs.addTab(self.spectrum_panel, "Spectrum")
        self.tabs.addTab(self.monitor_panel, "Monitor")

    def _build_left_dock(self) -> None:
        self.controls_dock = QDockWidget("Controls", self)
        self.controls_dock.setObjectName("controls_dock")
        self.controls_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.controls_dock.setMinimumWidth(285)

        self.acquisition_panel = AcquisitionPanel(self)
        self.acquisition_panel.acquire_requested.connect(self.take_spectrum)
        self.acquisition_panel.background_requested.connect(self.capture_background)
        self.acquisition_panel.background_clear_requested.connect(
            self.background_clear_requested.emit
        )
        self.acquisition_panel.live_changed.connect(self._on_live_changed)

        self.laser_panel = LaserPanel(self)
        self.scan_panel = ScanPanel(self)
        self.filter_wheel_panel = FilterWheelPanel(self)

        lower_tabs = QTabWidget()
        lower_tabs.addTab(self.laser_panel, "Lasers")
        lower_tabs.addTab(self.scan_panel, "Scan")
        lower_tabs.addTab(self.filter_wheel_panel, "Filters")

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("controls_splitter")
        splitter.addWidget(self.acquisition_panel)
        splitter.addWidget(lower_tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.controls_dock.setWidget(splitter)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.controls_dock)

    def _build_power_dock(self) -> None:
        self.power_dock = QDockWidget("Power", self)
        self.power_dock.setObjectName("power_dock")
        self.power_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.power_dock.setMinimumWidth(180)

        self.power_panel = PowerPanel(
            max_points=int(self.power_monitor_settings.max_points),
            parent=self,
        )
        self.power_panel.clear_requested.connect(self.clear_power_trace)
        self.power_panel.details_requested.connect(self.show_power_details_dialog)
        self.power_panel.mode_changed.connect(self._on_power_monitor_mode_changed)
        self.power_panel.auto_wavelength_changed.connect(
            self._on_auto_power_meter_wavelength_changed
        )
        self.power_panel.wavelength_set_requested.connect(
            self.power_meter_wavelength_requested.emit
        )

        self.power_dock.setWidget(self.power_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.power_dock)

    def _build_status_bar(self) -> None:
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Starting device controller...")

        self.autosave_label = QLabel()
        self.statusBar().addPermanentWidget(self.autosave_label)
        self._update_autosave_indicator()

        self.performance_label = QLabel()
        self.statusBar().addPermanentWidget(self.performance_label)

    def _build_file_io_controller(self) -> None:
        self.file_io = FileIOController(
            parent=self,
            file_settings=self.file_name_settings,
            power_settings=self.power_monitor_settings,
            monitor_panel=self.monitor_panel,
            power_panel=self.power_panel,
        )
        self.file_io.record_loaded.connect(self._on_record_loaded)
        self.file_io.status_requested.connect(self._show_status_with_timeout)
        self.file_io.power_log_state_changed.connect(
            self._on_power_log_state_changed
        )
        self.monitor_panel.save_requested.connect(self.file_io.save_monitor_track)
        self.power_panel.save_requested.connect(self.file_io.save_power_trace)

    def _build_actions(self) -> None:
        actions = build_main_window_actions(self)
        self.open_action = actions.open_spectrum
        self.save_action = actions.save_spectrum
        self.save_track_action = actions.save_monitor
        self.save_power_trace_action = actions.save_power_trace
        self.start_power_log_action = actions.start_power_log
        self.stop_power_log_action = actions.stop_power_log
        self.acquire_action = actions.acquire
        self.spectrum_auto_range_action = actions.spectrum_auto_range
        self.scan_timing_action = actions.scan_timing
        self.acquisition_toolbar = actions.toolbar
        self.power_label = actions.power_label

    def _build_timers(self) -> None:
        self.live_next_timer = QTimer(self)
        self.live_next_timer.setSingleShot(True)
        self.live_next_timer.timeout.connect(self._start_live_acquisition_if_ready)

        self.power_timer = QTimer(self)
        self.power_timer.timeout.connect(self._poll_power_tick)

    def _build_scan_coordinator(self) -> None:
        self.scan_coordinator = ScanCoordinator(
            parent=self,
            config=self.config,
            scan_panel=self.scan_panel,
            laser_panel=self.laser_panel,
            acquisition_panel=self.acquisition_panel,
            filter_wheel_panel=self.filter_wheel_panel,
        )
        self.scan_panel.preview_requested.connect(
            self.scan_coordinator.preview_power_scan
        )
        self.scan_panel.run_requested.connect(self.scan_coordinator.start_power_scan)
        self.scan_panel.abort_requested.connect(self.scan_coordinator.abort_power_scan)
        self.scan_panel.calibration_requested.connect(
            self.scan_coordinator.start_calibration_scan
        )
        self.scan_panel.save_calibration_requested.connect(
            self.scan_coordinator.save_current_calibration
        )
        self.scan_panel.load_calibration_requested.connect(
            self.scan_coordinator.load_calibration
        )
        self.scan_coordinator.laser_set_power_requested.connect(
            self.laser_set_power_requested.emit
        )
        self.scan_coordinator.laser_set_enabled_requested.connect(
            self.laser_set_enabled_requested.emit
        )
        self.scan_coordinator.power_read_once_requested.connect(
            self.power_read_once_requested.emit
        )
        self.scan_coordinator.spectrum_requested.connect(self.take_spectrum)
        self.scan_coordinator.autosave_requested.connect(self._autosave_spectrum)
        self.scan_coordinator.auto_wavelength_requested.connect(
            self._maybe_update_power_meter_wavelength_for_laser
        )
        self.scan_coordinator.status_requested.connect(self._show_status_with_timeout)

    def _build_preferences_controller(self) -> None:
        self.preferences = PreferencesController(
            window=self,
            file_settings=self.file_name_settings,
            power_settings=self.power_monitor_settings,
            warning_settings=self.signal_warning_settings,
            plot_settings=self.plot_style_settings,
            display_settings=self.display_settings,
            snr_settings=self.snr_settings,
            acquisition_panel=self.acquisition_panel,
            monitor_panel=self.monitor_panel,
            power_panel=self.power_panel,
            laser_panel=self.laser_panel,
            scan_panel=self.scan_panel,
            filter_wheel_panel=self.filter_wheel_panel,
        )

    # ----------------------------------------------------------- worker controllers

    def _start_instrument_runtime(self) -> None:
        self.runtime = InstrumentRuntime(self.config, self)

        # GUI/coordinator requests are routed through the runtime. The runtime
        # owns the worker threads and guarantees queued hardware calls.
        self.acquire_requested.connect(self.runtime.acquire)
        self.power_poll_requested.connect(self.runtime.poll_power)
        self.power_settings_changed.connect(self.runtime.set_power_monitor_settings)
        self.power_meter_wavelength_requested.connect(
            self.runtime.set_power_meter_wavelength_nm
        )
        self.power_read_once_requested.connect(self.runtime.read_power_once)
        self.background_capture_requested.connect(self.runtime.capture_background)
        self.background_clear_requested.connect(self.runtime.clear_background)
        self.tec_target_requested.connect(self.runtime.set_tec_target_c)
        self.tec_enabled_requested.connect(self.runtime.set_tec_enabled)
        self.spectrometer_temperature_requested.connect(
            self.runtime.query_spectrometer_temperature
        )
        self.spectrometer_capabilities_requested.connect(
            self.runtime.query_spectrometer_capabilities
        )
        self.laser_set_power_requested.connect(self.runtime.set_laser_power_w)
        self.laser_set_enabled_requested.connect(self.runtime.set_laser_enabled)

        self.laser_panel.refresh_requested.connect(self.runtime.refresh_lasers)
        self.laser_panel.set_power_requested.connect(self.runtime.set_laser_power_w)
        self.laser_panel.set_enabled_requested.connect(
            self._on_laser_enable_requested
        )
        self.laser_panel.disable_all_requested.connect(
            self.runtime.disable_all_lasers
        )
        self.laser_panel.set_cdrh_delay_requested.connect(
            self.runtime.set_laser_cdrh_delay
        )

        # Device-controller results.
        self.runtime.connected.connect(self._on_connected)
        self.runtime.connection_failed.connect(self._on_connection_failed)
        self.runtime.spectrum_ready.connect(self._on_spectrum_ready)
        self.runtime.acquisition_failed.connect(self._on_acquisition_failed)
        self.runtime.power_ready.connect(self._on_power_ready)
        self.runtime.power_read_complete.connect(
            self.scan_coordinator.on_power_read_complete
        )
        self.runtime.power_read_failed.connect(
            self.scan_coordinator.on_power_read_failed
        )
        self.runtime.power_meter_wavelength_ready.connect(
            self._on_power_meter_wavelength_ready
        )
        self.runtime.spectrometer_info_ready.connect(self._on_spectrometer_info)
        self.runtime.spectrometer_capabilities_ready.connect(
            self._on_spectrometer_capabilities_ready
        )
        self.runtime.spectrometer_temperature_ready.connect(
            self._on_spectrometer_temperature_ready
        )
        self.runtime.background_ready.connect(self._on_background_ready)
        self.runtime.background_cleared.connect(self._on_background_cleared)
        self.runtime.device_status.connect(self._show_status_message)
        self.runtime.device_error.connect(self._on_worker_error)

        # Laser-controller results.
        self.runtime.lasers_ready.connect(self._on_lasers_ready)
        self.runtime.laser_power_set_complete.connect(
            self.laser_panel.update_setpoint
        )
        self.runtime.laser_power_set_complete.connect(
            self.scan_coordinator.on_laser_power_set
        )
        self.runtime.laser_enabled_set_complete.connect(
            self.laser_panel.update_enabled
        )
        self.runtime.laser_enabled_set_complete.connect(
            self.scan_coordinator.on_laser_enabled_set
        )
        self.runtime.laser_power_set_failed.connect(
            self.scan_coordinator.on_laser_power_set_failed
        )
        self.runtime.laser_enabled_set_failed.connect(
            self.laser_panel.restore_enabled_after_failure
        )
        self.runtime.laser_enabled_set_failed.connect(
            self.scan_coordinator.on_laser_enabled_set_failed
        )
        self.runtime.laser_cdrh_set_complete.connect(self.laser_panel.update_cdrh)
        self.runtime.laser_cdrh_set_failed.connect(
            self.laser_panel.restore_cdrh_after_failure
        )
        self.runtime.laser_status.connect(self._show_status_message)
        self.runtime.laser_error.connect(self._on_laser_error)

        self.runtime.start()

    # ---------------------------------------------------------------- acquisition

    def _settings(self) -> AcquisitionSettings:
        settings = self.acquisition_panel.settings(
            run_identifier=self.file_name_settings.run_identifier,
            notes=self.file_name_settings.notes,
        )
        return self.scan_coordinator.apply_scan_metadata(settings)

    @Slot()
    def take_spectrum(self) -> None:
        if self.acquiring:
            return
        self.acquiring = True
        self.acquire_action.setEnabled(False)
        self.acquisition_panel.set_acquiring(True)
        settings = self._settings()
        self.statusBar().showMessage(
            f"Acquiring spectrum: {settings.integration_ms} ms, "
            f"avg={settings.averages}, boxcar={settings.boxcar_width}",
            5000,
        )
        self.scan_coordinator.timer.log("emit acquire request")
        self.acquire_requested.emit(settings)

    @Slot()
    def _live_tick(self) -> None:
        if self.acquisition_panel.is_live_enabled() and not self.acquiring:
            self.take_spectrum()

    @Slot(bool)
    def _on_live_changed(self, enabled: bool) -> None:
        self.live_next_timer.stop()
        if enabled:
            self._schedule_next_live_acquisition()
        self.statusBar().showMessage(
            f"Live acquisition: {'ON' if enabled else 'OFF'}",
            5000,
        )

    def _schedule_next_live_acquisition(self) -> None:
        if not self.acquisition_panel.is_live_enabled() or self.acquiring:
            return
        if self.scan_coordinator.power_scan_active or self.scan_coordinator.calibration_active:
            return
        self.live_next_timer.start(max(0, int(self.display_settings.live_acquisition_gap_ms)))

    @Slot()
    def _start_live_acquisition_if_ready(self) -> None:
        if not self.acquisition_panel.is_live_enabled() or self.acquiring:
            return
        if self.scan_coordinator.power_scan_active or self.scan_coordinator.calibration_active:
            return
        self.take_spectrum()

    @Slot(object)
    def _on_spectrum_ready(self, record: SpectrumRecord) -> None:
        self._finish_acquisition_ui()
        self.performance_monitor.mark_acquisition()
        self.current_record = record
        self.file_io.set_current_record(record)
        self.spectrum_panel.queue_record(record)
        self._display_power_snapshot(record.p_after)

        if (self.snr_settings.enabled and record.snr is not None):
            self.acquisition_panel.set_snr(record.snr)

        if self.power_monitor_settings.append_spectrum_power:
            self._append_power_history(
                record.mean_power_snapshot(),
                source="spectrum_mean",
            )

        self._check_signal_warning(record)
        if self.monitor_panel.tracking_enabled():
            self.monitor_panel.add_record(record)

        if self.file_name_settings.autosave_spectra and not self.scan_coordinator.power_scan_active:
            self._autosave_spectrum(record)

        self.statusBar().showMessage(
            f"Spectrum acquired: {record.timestamp_utc}, mean ch1 power "
            f"{format_power_w(record.mean_power_w(0))}",
            10_000,
        )
        self.scan_coordinator.handle_spectrum_ready(record)
        self._schedule_next_live_acquisition()

    @Slot(str)
    def _on_acquisition_failed(self, message: str) -> None:
        self._finish_acquisition_ui()
        self.acquisition_panel.set_live_enabled(False)
        self.scan_coordinator.handle_acquisition_failed(message)
        self.live_next_timer.stop()
        self.statusBar().showMessage(
            "Spectrum acquisition failed. Live acquisition stopped.",
            15_000,
        )
        print(message, file=sys.stderr)
        QMessageBox.warning(
            self,
            "Spectrum acquisition failed",
            message.splitlines()[-1] if message else "Unknown acquisition error.",
        )

    def _finish_acquisition_ui(self) -> None:
        self.acquiring = False
        self.acquire_action.setEnabled(True)
        self.acquisition_panel.set_acquiring(False)

    def capture_background(self) -> None:
        if self.acquiring:
            return
        self.acquiring = True
        self.acquire_action.setEnabled(False)
        self.acquisition_panel.set_acquiring(True)
        settings = replace(self._settings(), subtract_background=False)
        self.statusBar().showMessage("Capturing background spectrum...", 5000)
        self.background_capture_requested.emit(settings)

    # ------------------------------------------------------------------- power

    @Slot(object)
    def _on_power_ready(self, power: PowerSnapshot) -> None:
        if self.power_monitor_settings.mode != "live":
            return
        self._display_power_snapshot(power)
        self._append_power_history(power, source="poll")

    def _display_power_snapshot(self, power: PowerSnapshot) -> None:
        self.power_panel.set_current_power(power)
        ch1 = power.powers_w[0] if power.powers_w else float("nan")
        self.power_label.setText(f"Power: {format_power_w(ch1)}")

    def _append_power_history(self, power: PowerSnapshot, *, source: str) -> None:
        point = PowerTracePoint(
            timestamp_utc=utc_now_iso(),
            elapsed_s=float(time.perf_counter() - self.app_t0),
            source=str(source),
            powers_w=[float(value) for value in power.powers_w],
            pm_status=[int(value) for value in power.pm_status],
            command_status=int(power.command_status),
        )
        self.file_io.write_power_point(point)
        self.power_panel.append_point(point)

    def _poll_power_tick(self) -> None:
        # DeviceController currently serializes Newport and spectrometer work in
        # one worker thread. Avoid building a queue of stale live-poll requests
        # behind a long spectrum acquisition; spectrum-associated before/after
        # readings are still recorded by DeviceController.acquire().
        if self.acquiring:
            return
        if self.power_monitor_settings.live_polling_enabled:
            self.power_poll_requested.emit()

    @Slot(str)
    def _on_power_monitor_mode_changed(self, mode: str) -> None:
        mode = mode if mode in {"live", "spectra_only"} else "live"
        self.power_monitor_settings.mode = mode
        self._apply_power_monitor_settings()
        self.statusBar().showMessage(
            f"Power monitor mode: {'live readings' if mode == 'live' else 'spectra only'}.",
            5000,
        )

    def _apply_power_monitor_settings(self) -> None:
        self.power_panel.set_max_points(self.power_monitor_settings.max_points)
        self.power_panel.set_mode(self.power_monitor_settings.mode)
        self.power_timer.setInterval(self.power_monitor_settings.interval_ms)
        if self.power_monitor_settings.live_polling_enabled:
            self.power_timer.start()
        else:
            self.power_timer.stop()
        self.power_settings_changed.emit(replace(self.power_monitor_settings))

    @Slot(bool)
    def _on_auto_power_meter_wavelength_changed(self, enabled: bool) -> None:
        self.auto_update_power_meter_wavelength = bool(enabled)
        self.statusBar().showMessage(
            f"Auto Newport wavelength update {'enabled' if enabled else 'disabled'}.",
            5000,
        )

    def _maybe_update_power_meter_wavelength_for_laser(
        self,
        laser: LaserChannelInfo,
    ) -> None:
        if not self.auto_update_power_meter_wavelength:
            return
        wavelength = float(laser.wavelength_nm)
        if not math.isfinite(wavelength):
            return
        wavelength_nm = int(round(wavelength))
        if wavelength_nm == self.last_power_meter_wavelength_nm:
            return
        self.power_panel.set_power_meter_wavelength_nm(wavelength_nm)
        self.power_meter_wavelength_requested.emit(wavelength_nm)

    @Slot(int)
    def _on_power_meter_wavelength_ready(self, wavelength_nm: int) -> None:
        self.last_power_meter_wavelength_nm = int(wavelength_nm)
        self.power_panel.set_power_meter_wavelength_nm(wavelength_nm)
        self.statusBar().showMessage(
            f"Newport wavelength: {int(wavelength_nm)} nm",
            5000,
        )

    # ------------------------------------------------------------- laser callbacks

    @Slot(object)
    def _on_lasers_ready(self, lasers: object) -> None:
        self.laser_panel.set_lasers(lasers)

    @Slot(str, int, bool)
    def _on_laser_enable_requested(
        self,
        port: str,
        channel: int,
        enabled: bool,
    ) -> None:
        laser = self.laser_panel.laser_by_key(port, channel)
        if enabled and laser is not None:
            self._maybe_update_power_meter_wavelength_for_laser(laser)
        self.laser_set_enabled_requested.emit(port, channel, enabled)

    # --------------------------------------------------------- device/status slots

    @Slot(str)
    def _on_connected(self, message: str) -> None:
        self.statusBar().showMessage(message, 10_000)

    @Slot(str)
    def _on_connection_failed(self, message: str) -> None:
        self.statusBar().showMessage("Device connection failed.")
        QMessageBox.critical(self, "Device connection failed", message)

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        self._finish_acquisition_ui()
        self.statusBar().showMessage("Worker error. See console output.")
        print(message, file=sys.stderr)

    @Slot(str)
    def _on_laser_error(self, message: str) -> None:
        print(message, file=sys.stderr)
        self.statusBar().showMessage(
            "Laser controller error. See console output.",
            10_000,
        )

    @Slot(str)
    def _show_status_message(self, message: str) -> None:
        self.statusBar().showMessage(str(message), 10_000)

    @Slot(str, int)
    def _show_status_with_timeout(self, message: str, timeout_ms: int) -> None:
        self.statusBar().showMessage(str(message), int(timeout_ms))

    @Slot(object)
    def _on_spectrometer_info(self, info: SpectrometerInfo) -> None:
        self.spectrometer_info = info
        if math.isfinite(info.max_intensity):
            message = (
                f"Spectrometer: {info.name}, serial {info.serial_number or '--'}, "
                f"max intensity {info.max_intensity:.0f} counts"
            )
        else:
            message = f"Spectrometer: {info.name}, serial {info.serial_number or '--'}"
        self.statusBar().showMessage(message, 10_000)

    @Slot(object)
    def _on_spectrometer_capabilities_ready(
        self,
        capabilities: SpectrometerCapabilities,
    ) -> None:
        self.spectrometer_capabilities = capabilities
        self.acquisition_panel.set_integration_limits_us(
            capabilities.integration_time_min_us,
            capabilities.integration_time_max_us,
        )

    @Slot(object)
    def _on_background_ready(self, background: BackgroundSpectrum) -> None:
        self._finish_acquisition_ui()
        self.statusBar().showMessage(
            f"Background captured: {background.timestamp_utc}, "
            f"{background.integration_ms} ms, avg={background.averages}",
            15_000,
        )

    @Slot()
    def _on_background_cleared(self) -> None:
        self.statusBar().showMessage("Background spectrum cleared.", 10_000)

    @Slot(float)
    def _on_spectrometer_temperature_ready(self, temperature_c: float) -> None:
        self.statusBar().showMessage(
            f"CCD temperature: {float(temperature_c):.2f} °C",
            10_000,
        )

    @Slot()
    def _on_monitor_cleared(self) -> None:
        self.statusBar().showMessage("Spectrum monitor cleared.", 5000)

    # -------------------------------------------------------------- signal checks

    def _signal_warning_threshold_counts(self) -> float:
        settings = self.signal_warning_settings
        if settings.use_spectrometer_max:
            maximum = float(self.spectrometer_info.max_intensity)
            if math.isfinite(maximum) and maximum > 0:
                return settings.fraction_of_spectrometer_max * maximum
        return float(settings.absolute_threshold_counts)

    def _check_signal_warning(self, record: SpectrumRecord) -> None:
        settings = self.signal_warning_settings
        if not settings.enabled:
            return
        signal_max = float(record.signal_max_counts)
        if not math.isfinite(signal_max):
            signal_max = float(np.nanmax(record.intensities_counts))
        threshold = self._signal_warning_threshold_counts()
        if not math.isfinite(threshold) or threshold <= 0 or signal_max < threshold:
            return

        detector_max = float(self.spectrometer_info.max_intensity)
        if math.isfinite(detector_max) and detector_max > 0:
            message = (
                "High spectrometer signal detected.\n\n"
                f"Maximum signal: {signal_max:.0f} counts\n"
                f"Spectrometer max: {detector_max:.0f} counts\n"
                f"Fraction of max: {100.0 * signal_max / detector_max:.2f}%\n"
                f"Warning threshold: {threshold:.0f} counts"
            )
        else:
            message = (
                "High spectrometer signal detected.\n\n"
                f"Maximum signal: {signal_max:.0f} counts\n"
                f"Warning threshold: {threshold:.0f} counts"
            )
        self.statusBar().showMessage(message.replace("\n", " "), 15_000)
        if not settings.popup_enabled:
            return
        now = time.perf_counter()
        if now - self.last_signal_warning_s < settings.popup_cooldown_s:
            return
        self.last_signal_warning_s = now
        QMessageBox.warning(self, "High spectrometer signal", message)

    @Slot(float, int)
    def _on_monitor_memory_warning(self, estimated_mb: float, n_points: int) -> None:
        QMessageBox.warning(
            self,
            "Monitor memory warning",
            f"The scalar monitor is estimated to use about {estimated_mb:.1f} MB.\n\n"
            f"Current points: {n_points}\n\nClear it if the full history is not needed.",
        )

    # -------------------------------------------------------------- performance/snr

    @Slot(object)
    def _on_performance_updated(self, snapshot: PerformanceSnapshot) -> None:
        self.performance_label.setVisible(self.display_settings.performance_enabled)
        self.performance_label.setText(snapshot.format_status())

    def _apply_snr_settings(
        self,
        *,
        push_to_runtime: bool = True,
    ) -> None:
        """
        Apply the current SNR settings to both the acquisition-panel display
        and the instrument worker.

        During initial preference loading, the runtime may not exist yet, so
        push_to_runtime can be disabled.
        """

        self.acquisition_panel.set_snr_enabled(
            bool(self.snr_settings.enabled)
        )

        if push_to_runtime and hasattr(self, "runtime"):
            self.runtime.set_snr_settings(
                replace(self.snr_settings)
            )

    # ------------------------------------------------------------- plot/view tools

    def _apply_display_settings(self) -> None:
        self.spectrum_panel.set_redraw_interval_ms(
            self.display_settings.spectrum_redraw_interval_ms
        )

        self.monitor_panel.set_redraw_interval_ms(
            self.display_settings.monitor_redraw_interval_ms
        )

        self.power_panel.set_redraw_interval_ms(
            self.display_settings.power_redraw_interval_ms
        )

        self.performance_monitor.configure(
            enabled=self.display_settings.performance_enabled,
            rate_window_s=self.display_settings.performance_rate_window_s,
            report_interval_ms=self.display_settings.performance_report_interval_ms,
            probe_interval_ms=self.display_settings.event_loop_probe_interval_ms,
        )

        self.performance_label.setVisible(
            self.display_settings.performance_enabled
        )

    @Slot(bool)
    def _on_spectrum_auto_range_toggled(self, enabled: bool) -> None:
        self.plot_style_settings.spectrum_auto_range = bool(enabled)
        self.spectrum_panel.set_auto_range(enabled)
        self._save_preferences()

    def show_spectrum_axis_dialog(self) -> None:
        dialog = SpectrumAxisDialog(self.plot_style_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.spectrum_auto_range_action.setChecked(
            self.plot_style_settings.spectrum_auto_range
        )
        self._apply_plot_style()
        self._save_preferences()

    def use_current_spectrum_view_as_limits(self) -> None:
        x_min, x_max, y_min, y_max = self.spectrum_panel.current_view_range()
        self.plot_style_settings.spectrum_auto_range = False
        self.plot_style_settings.spectrum_x_min = x_min
        self.plot_style_settings.spectrum_x_max = x_max
        self.plot_style_settings.spectrum_y_min = y_min
        self.plot_style_settings.spectrum_y_max = y_max
        self.spectrum_auto_range_action.setChecked(False)
        self.spectrum_panel.apply_style(self.plot_style_settings)
        self._save_preferences()

    def _apply_plot_style(self) -> None:
        self.spectrum_panel.apply_style(self.plot_style_settings)
        self.monitor_panel.apply_plot_style(self.plot_style_settings)
        self.power_panel.apply_plot_style(self.plot_style_settings)

    # -------------------------------------------------------------- clear / toggle

    def clear_spectrum(self) -> None:
        self.current_record = None
        self.file_io.set_current_record(None)
        self.spectrum_panel.clear()
        self.statusBar().showMessage("Spectrum cleared.", 5000)

    def clear_monitor_track(self) -> None:
        self.monitor_panel.clear()

    def clear_power_trace(self) -> None:
        self.power_panel.clear()
        self.statusBar().showMessage("Power trace cleared.", 5000)

    def clear_all_monitors(self) -> None:
        self.clear_power_trace()
        self.clear_monitor_track()
        self.statusBar().showMessage("Power and spectrum monitors cleared.", 5000)

    def toggle_live(self) -> None:
        self.acquisition_panel.set_live_enabled(
            not self.acquisition_panel.is_live_enabled()
        )

    # -------------------------------------------------------------------- dialogs

    def open_settings_dialog(self) -> None:
        dialog = AppSettingsDialog(
            self,
            self.file_name_settings,
            self.power_monitor_settings,
            self.signal_warning_settings,
            self.plot_style_settings,
            self.spectrometer_info,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        (
            self.file_name_settings,
            self.power_monitor_settings,
            self.signal_warning_settings,
            self.plot_style_settings,
        ) = dialog.settings()
        self.file_io.update_settings(
            self.file_name_settings,
            self.power_monitor_settings,
        )
        self.preferences.update_dataclasses(
            self.file_name_settings,
            self.power_monitor_settings,
            self.signal_warning_settings,
            self.plot_style_settings,
            self.display_settings,
            self.snr_settings,
        )
        self._apply_power_monitor_settings()
        self._apply_plot_style()
        self._update_autosave_indicator()
        self._save_preferences()
        self.statusBar().showMessage("Settings updated.", 5000)

    def show_display_settings_dialog(self) -> None:
        old_theme = self.display_settings.theme_name

        dialog = DisplaySettingsDialog(
            self.display_settings,
            self,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.display_settings = dialog.settings()
        self._apply_display_settings()

        self.preferences.update_dataclasses(
            self.file_name_settings,
            self.power_monitor_settings,
            self.signal_warning_settings,
            self.plot_style_settings,
            self.display_settings,
            self.snr_settings,
        )

        self._save_preferences()

        if self.display_settings.theme_name != old_theme:
            result = QMessageBox.question(
                self,
                "Restart to apply theme",
                "The theme requires a restart.\n\n"
                "Restart the GUI now?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
            )

            if result == QMessageBox.StandardButton.Yes:
                QTimer.singleShot(
                    0,
                    self.request_application_restart,
                )

    def show_performance_settings_dialog(self) -> None:
        dialog = PerformanceSettingsDialog(
            self.display_settings,
            self,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.display_settings = dialog.settings()
        self._apply_display_settings()

        self.preferences.update_dataclasses(
            self.file_name_settings,
            self.power_monitor_settings,
            self.signal_warning_settings,
            self.plot_style_settings,
            self.display_settings,
            self.snr_settings,
        )

        self._save_preferences()

    def show_power_details_dialog(self) -> None:
        PowerDetailsDialog(self.power_panel.points(), self).exec()

    def show_snr_settings_dialog(self) -> None:
        dialog = SNRSettingsDialog(
            self.snr_settings,
            self,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.snr_settings = dialog.settings()
        self._apply_snr_settings()

        self.preferences.update_dataclasses(
            self.file_name_settings,
            self.power_monitor_settings,
            self.signal_warning_settings,
            self.plot_style_settings,
            self.display_settings,
            self.snr_settings,
        )

        self._save_preferences()

    def show_spectrometer_details_dialog(self) -> None:
        if self.spectrometer_capabilities is None:
            self.spectrometer_capabilities_requested.emit()
            QMessageBox.information(
                self,
                "Spectrometer details requested",
                "Capabilities were requested. Reopen this dialog in a moment.",
            )
            return
        dialog = SpectrometerDetailsDialog(self.spectrometer_capabilities, self)
        dialog.tec_target_requested.connect(self.tec_target_requested.emit)
        dialog.tec_enabled_requested.connect(self.tec_enabled_requested.emit)
        dialog.temperature_refresh_requested.connect(
            self.spectrometer_temperature_requested.emit
        )
        dialog.exec()

    # ---------------------------------------------------------------- file I/O

    @Slot(object)
    def _on_record_loaded(self, record: SpectrumRecord) -> None:
        self.current_record = record
        self.file_io.set_current_record(record)
        self.spectrum_panel.show_arrays(
            record.wavelengths_nm,
            record.intensities_counts,
        )
        self.tabs.setCurrentWidget(self.spectrum_panel)

    @Slot(bool)
    def _on_power_log_state_changed(self, active: bool) -> None:
        self.start_power_log_action.setEnabled(not active)
        self.stop_power_log_action.setEnabled(active)

    @Slot(object)
    def _autosave_spectrum(self, record: SpectrumRecord) -> None:
        self.scan_coordinator.timer.log("autosave start")
        self.file_io.autosave_spectrum(record)
        self.scan_coordinator.timer.log("autosave complete")

    # --------------------------------------------------------------- preferences

    def _load_preferences(self) -> None:
        (
            self.auto_update_power_meter_wavelength,
            self._loaded_scan_timing,
        ) = self.preferences.load()

    def _save_preferences(self) -> None:
        self.preferences.save(scan_timing=self.scan_timing_action.isChecked())

    def _apply_loaded_preferences(self) -> None:
        self.power_panel.set_mode(
            self.power_monitor_settings.mode
        )
        self.power_panel.set_auto_wavelength_enabled(
            self.auto_update_power_meter_wavelength
        )

        self.spectrum_auto_range_action.setChecked(
            self.plot_style_settings.spectrum_auto_range
        )

        self.scan_timing_action.setChecked(
            self._loaded_scan_timing
        )
        self.scan_coordinator.set_timing_enabled(
            self._loaded_scan_timing
        )

        self._apply_plot_style()
        self._apply_display_settings()
        self._apply_snr_settings(push_to_runtime=False)
        self._update_autosave_indicator()

    def _restore_window_layout(self) -> bool:
        return self.preferences.restore_window_layout()

    def _save_window_layout(self) -> None:
        self.preferences.save_window_layout()

    def _apply_initial_layout(self) -> None:
        self.resizeDocks(
            [self.controls_dock, self.power_dock],
            [315, 210],
            Qt.Orientation.Horizontal,
        )

    def reset_window_layout(self) -> None:
        self.preferences.clear_window_layout()
        self.controls_dock.show()
        self.power_dock.show()
        self.resize(1600, 850)
        self._apply_initial_layout()

    def _update_autosave_indicator(self) -> None:
        self.autosave_label.setText(
            "Autosave: ON" if self.file_name_settings.autosave_spectra else "Autosave: OFF"
        )

    @Slot(bool)
    def _on_scan_timing_toggled(self, enabled: bool) -> None:
        self.scan_coordinator.set_timing_enabled(enabled)
        self._save_preferences()

    # ----------------------------------------------------------------------- help

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Spectrometer GUI",
            "Spectrometer GUI\n\n"
            "Acquisition and scan control for QEPro, Newport 2936-R, and "
            "Coherent OBIS laser systems.",
        )

    def show_shortcuts(self) -> None:
        QMessageBox.information(
            self,
            "Keyboard Shortcuts",
            "F5: Take spectrum\n"
            "Ctrl+L: Toggle live acquisition\n"
            "Ctrl+O: Open spectrum\n"
            "Ctrl+S: Save spectrum\n"
            "Ctrl+Shift+C: Clear all monitors\n"
            "Ctrl+Q: Quit",
        )

    def open_github(self) -> None:
        QDesktopServices.openUrl(
            QUrl("https://github.com/cclark012/spectrometer-gui")
        )

    # -------------------------------------------------------------------- shutdown

    def request_application_restart(self) -> None:
        if self.acquiring:
            QMessageBox.information(
                self,
                "Acquisition active",
                "Stop the current acquisition before restarting the GUI.",
            )
            return

        if (
            self.scan_coordinator.power_scan_active
            or self.scan_coordinator.calibration_active
        ):
            QMessageBox.information(
                self,
                "Scan active",
                "Stop the active scan before restarting the GUI.",
            )
            return

        self._application_exit_code = RESTART_EXIT_CODE
        self.close()

    def closeEvent(self, event) -> None:
        self._save_preferences()
        self._save_window_layout()
        self.acquisition_panel.set_live_enabled(False)
        self.live_next_timer.stop()
        self.power_timer.stop()
        self.file_io.close()
        self.runtime.shutdown()
        super().closeEvent(event)
        event.accept()

        exit_code = int(self._application_exit_code)

        QTimer.singleShot(
            0,
            lambda: QApplication.exit(exit_code),
        )
