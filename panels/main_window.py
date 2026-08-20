from __future__ import annotations

import math
import sys
import time
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QSettings, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QDialog,
    QDockWidget,
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTabWidget,
)

from controllers.auto_acquisition_coordinator import AutoAcquisitionCoordinator
from controllers.file_io_controller import FileIOController
from controllers.gated_acquisition_coordinator import GatedAcquisitionCoordinator
from controllers.instrument_runtime import InstrumentRuntime
from controllers.preferences_controller import PreferencesController
from controllers.scan_coordinator import ScanCoordinator
from core.laser_models import LaserChannelInfo
from core.performance import PerformanceMonitor, PerformanceSnapshot
from core.records import (
    BackgroundSpectrum,
    InstrumentConnectionState,
    PowerSnapshot,
    PowerTracePoint,
    SpectrometerCapabilities,
    SpectrometerInfo,
    SpectrumRecord,
)
from core.restart import RESTART_EXIT_CODE
from core.sequence_arbiter import AUTOMATED_SEQUENCE_OWNERS, SequenceArbiter
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
from dialogs.acquisition_recommendation_dialog import (
    AcquisitionRecommendationDialog,
    RecommendationChoice,
)
from dialogs.display_settings_dialog import DisplaySettingsDialog
from dialogs.instrument_connections_dialog import InstrumentConnectionsDialog
from dialogs.performance_settings_dialog import PerformanceSettingsDialog
from dialogs.power_details_dialog import PowerDetailsDialog
from dialogs.settings_dialog import AppSettingsDialog
from dialogs.snr_settings_dialog import SNRSettingsDialog
from dialogs.spectrometer_details_dialog import SpectrometerDetailsDialog
from dialogs.spectrum_axis_dialog import SpectrumAxisDialog
from dialogs.theme_editor_dialog import ThemeEditorDialog
from dialogs.theme_preview_dialog import ThemePreviewDialog
from panels.acquisition_panel import AcquisitionPanel
from panels.filter_wheels_panel import FilterWheelPanel
from panels.gated_acquisition_panel import GatedAcquisitionPanel
from panels.laser_panel import LaserPanel
from panels.main_window_actions import build_main_window_actions
from panels.monitor_panel import MonitorPanel
from panels.power_panel import PowerPanel
from panels.scan_panel import ScanPanel
from panels.spectrum_panel import SpectrumPanel
from processing.snr import suggest_acquisition
from ui.theme import ThemeManager
from ui.window_geometry import clamp_main_window_to_available_screen


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

    def __init__(self, config: DeviceConfig, *, theme_manager: ThemeManager) -> None:
        super().__init__()
        self.theme_manager = theme_manager

        self.setWindowTitle("Magneto-PL Spectrum Acquisition")
        self.resize(1600, 850)

        self._application_exit_code = 0
        self._closing = False
        self.config = config
        self.file_name_settings = FileNameSettings()
        self.power_monitor_settings = PowerMonitorSettings()
        self.signal_warning_settings = SignalWarningSettings()
        self.plot_style_settings = PlotStyleSettings()
        self.display_settings = DisplaySettings()
        self.snr_settings = SNRSettings()
        self.spectrometer_info = SpectrometerInfo()
        self.spectrometer_capabilities: SpectrometerCapabilities | None = None

        self.instrument_states = {
            "spectrometer": InstrumentConnectionState(
                key="spectrometer",
                connected=False,
            ),
            "power_meter": InstrumentConnectionState(
                key="power_meter",
                connected=False,
            ),
            "lasers": InstrumentConnectionState(
                key="lasers",
                connected=False,
            ),
        }

        self.app_t0 = time.perf_counter()
        self.current_record: SpectrumRecord | None = None
        self.acquiring = False
        self._acquisition_owner: str | None = None
        self.sequence_arbiter = SequenceArbiter()
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
        self._build_performance_monitor()

        self._load_preferences()
        self._apply_loaded_preferences()

        self._start_instrument_runtime()

        self._apply_snr_settings()
        self._apply_power_monitor_settings()
        self._refresh_sequence_controls()

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

        self.spectrum_tab_index = self.tabs.addTab(
            self.spectrum_panel,
            "Spectrum",
        )

        self.monitor_tab_index = self.tabs.addTab(
            self.monitor_panel,
            "Monitor",
        )

    def _build_left_dock(self) -> None:
        self.controls_dock = QDockWidget("Controls", self)
        self.controls_dock.setObjectName("controls_dock")
        self.controls_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.controls_dock.setMinimumWidth(250)

        self.acquisition_panel = AcquisitionPanel(self)
        self.acquisition_panel.acquire_requested.connect(self.take_spectrum)
        self.acquisition_panel.background_requested.connect(self.capture_background)
        self.acquisition_panel.background_clear_requested.connect(
            self.background_clear_requested.emit
        )
        self.acquisition_panel.live_changed.connect(self._on_live_changed)

        self.acquisition_panel.recommend_acquisition_requested.connect(
                self.show_acquisition_recommendation
            )

        self.laser_panel = LaserPanel(self)
        self.scan_panel = ScanPanel(self)
        self.filter_wheel_panel = FilterWheelPanel(self)
        self.gated_panel = GatedAcquisitionPanel(self)

        self.lower_tabs = QTabWidget()
        self.laser_tab_index = self.lower_tabs.addTab(self.laser_panel, "Lasers")
        self.scan_tab_index = self.lower_tabs.addTab(self.scan_panel, "Scan")
        self.filter_tab_index = self.lower_tabs.addTab(self.filter_wheel_panel, "Filters")
        self.gated_tab_index = self.lower_tabs.addTab(self.gated_panel, "Gated")

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("controls_splitter")
        splitter.addWidget(self.acquisition_panel)
        splitter.addWidget(self.lower_tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        splitter.setMinimumSize(0, 0)

        self.controls_scroll = QScrollArea(self.controls_dock)
        self.controls_scroll.setObjectName("controls_scroll")
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.controls_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.controls_scroll.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        self.controls_scroll.setWidget(splitter)

        self.controls_dock.setWidget(self.controls_scroll)
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
        self.power_label_action = actions.power_label_action

    def _build_timers(self) -> None:
        self.live_next_timer = QTimer(self)
        self.live_next_timer.setSingleShot(True)
        self.live_next_timer.timeout.connect(self._start_live_acquisition_if_ready)

        self.power_timer = QTimer(self)
        self.power_timer.timeout.connect(self._poll_power_tick)

    def _build_scan_coordinator(self) -> None:
        # Create scan coordinator
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
        self.scan_panel.run_requested.connect(self.start_power_scan)
        self.scan_panel.abort_requested.connect(self.scan_coordinator.abort_power_scan)
        self.scan_panel.calibration_requested.connect(self.start_calibration_scan)
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
        self.scan_coordinator.spectrum_requested.connect(
            lambda: self._request_automated_spectrum("power_scan")
        )
        self.scan_coordinator.autosave_requested.connect(self._autosave_spectrum)
        self.scan_coordinator.auto_wavelength_requested.connect(
            self._maybe_update_power_meter_wavelength_for_laser
        )
        self.scan_coordinator.status_requested.connect(self._show_status_with_timeout)
        self.scan_coordinator.active_changed.connect(self._on_scan_active_changed)

        # Create auto-acquisition coordinator
        self.auto_acquisition = AutoAcquisitionCoordinator(self)
        self.acquisition_panel.auto_tune_acquisition_requested.connect(
            self.start_or_abort_auto_tune
        )
        self.auto_acquisition.apply_settings_requested.connect(
            lambda integration_ms, averages: self.acquisition_panel.set_acquisition_parameters(
                integration_ms=integration_ms,
                averages=averages,
            )
        )
        self.auto_acquisition.spectrum_requested.connect(
            lambda: self._request_automated_spectrum("auto_tune")
        )

        self.auto_acquisition.status_requested.connect(
            self._show_status_with_timeout
        )
        self.auto_acquisition.active_changed.connect(
            self._on_auto_tune_active_changed
        )
        self.auto_acquisition.completed.connect(
            self._on_auto_tune_completed
        )
        self.auto_acquisition.failed.connect(
            lambda message: QMessageBox.warning(self, "Auto Tune Failed", message)
        )

        # Create gated acquisition coordinator
        self.gated_coordinator = GatedAcquisitionCoordinator(self)
        self.gated_panel.preview_requested.connect(self.preview_gated_acquisition)
        self.gated_panel.run_requested.connect(self.start_gated_acquisition)
        self.gated_panel.abort_requested.connect(self.gated_coordinator.abort)
        self.gated_coordinator.laser_set_enabled_requested.connect(
            self.laser_set_enabled_requested.emit
        )
        self.gated_coordinator.plan_ready.connect(self.gated_panel.set_plan)
        self.gated_coordinator.spectrum_requested.connect(
            lambda: self._request_automated_spectrum("gated")
        )
        self.gated_coordinator.autosave_requested.connect(self._autosave_spectrum)
        self.gated_coordinator.status_requested.connect(self._show_status_with_timeout)
        self.gated_coordinator.active_changed.connect(
            self._on_gated_active_changed
        )
        self.gated_coordinator.failed.connect(self._on_gated_acquisition_failed)

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
            gated_panel=self.gated_panel,
            filter_wheel_panel=self.filter_wheel_panel,
        )

    def _build_performance_monitor(self) -> None:
        # Create performance monitor labels and tracking
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
            self._on_disable_all_lasers_requested
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
        self.runtime.power_meter_connection_changed.connect(
            self._on_instrument_connection_changed
        )
        self.runtime.spectrometer_info_ready.connect(self._on_spectrometer_info)
        self.runtime.spectrometer_capabilities_ready.connect(
            self._on_spectrometer_capabilities_ready
        )
        self.runtime.spectrometer_temperature_ready.connect(
            self._on_spectrometer_temperature_ready
        )
        self.runtime.spectrometer_connection_changed.connect(
            self._on_instrument_connection_changed
        )
        self.runtime.background_ready.connect(self._on_background_ready)
        self.runtime.background_cleared.connect(self._on_background_cleared)
        self.runtime.background_failed.connect(self._on_background_failed)
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
        self.runtime.laser_connection_changed.connect(
            self._on_instrument_connection_changed
        )
        self.auto_acquisition.snr_settings_requested.connect(
            self.runtime.set_snr_settings
        )
        self.runtime.laser_enabled_set_complete.connect(
            self.gated_coordinator.on_laser_enabled_set
        )
        self.runtime.laser_enabled_set_failed.connect(
            self.gated_coordinator.on_laser_enabled_failed
        )
        self.runtime.start()

    # ---------------------------------------------------------------- acquisition

    def _settings(self) -> AcquisitionSettings:
        settings = self.acquisition_panel.settings(
            run_identifier=self.file_name_settings.run_identifier,
            notes=self.file_name_settings.notes,
        )
        settings = self.scan_coordinator.apply_scan_metadata(settings)
        settings = self.gated_coordinator.apply_metadata(settings)
        return settings

    def _claim_sequence(self, owner: str) -> bool:
        if self.sequence_arbiter.claim(owner):
            self._refresh_sequence_controls()
            return True
        QMessageBox.information(
            self,
            "Sequence active",
            f"Stop the active {self.sequence_arbiter.label} first.",
        )
        return False

    def _release_sequence(self, owner: str) -> None:
        if self.sequence_arbiter.release(owner):
            self._refresh_sequence_controls()

    def _begin_sequence(self, owner: str) -> bool:
        if self.sequence_arbiter.owner == "live":
            self.acquisition_panel.set_live_enabled(False)
        if self.acquiring:
            QMessageBox.information(
                self,
                "Acquisition active",
                "Wait for the current instrument operation to finish.",
            )
            return False
        return self._claim_sequence(owner)

    def _refresh_sequence_controls(self) -> None:
        owner = self.sequence_arbiter.owner
        spectrometer = self._instrument_connected("spectrometer")
        automated = owner in AUTOMATED_SEQUENCE_OWNERS

        self.acquisition_panel.set_sequence_owner(owner)
        self.acquisition_panel.set_acquiring(self.acquiring)
        self.scan_panel.set_external_busy(
            owner is not None and owner not in {"power_scan", "calibration"}
        )
        self.gated_panel.set_external_busy(
            owner is not None and owner != "gated"
        )
        self.laser_panel.set_sequence_busy(automated)
        self.power_panel.set_sequence_busy(automated)
        self.acquire_action.setEnabled(
            spectrometer and owner is None and not self.acquiring
        )

    def _request_spectrum(self, owner: str) -> bool:
        if self.acquiring or not self._instrument_connected("spectrometer"):
            return False

        valid_request = {
            "manual": lambda: self.sequence_arbiter.owner in {None, "manual"},
            "live": lambda: (
                self.sequence_arbiter.owner == "live"
                and self.acquisition_panel.is_live_enabled()
            ),
            "power_scan": lambda: (
                self.sequence_arbiter.owner == "power_scan"
                and self.scan_coordinator.power_scan_active
            ),
            "gated": lambda: (
                self.sequence_arbiter.owner == "gated"
                and self.gated_coordinator.active
            ),
            "auto_tune": lambda: (
                self.sequence_arbiter.owner == "auto_tune"
                and self.auto_acquisition.active
            ),
        }.get(owner)
        if valid_request is None or not valid_request():
            return False
        if not self._claim_sequence(owner):
            return False

        self.acquiring = True
        self._acquisition_owner = owner
        self._refresh_sequence_controls()
        settings = self._settings()
        self.statusBar().showMessage(
            f"Acquiring spectrum: {settings.integration_ms} ms, "
            f"avg={settings.averages}, boxcar={settings.boxcar_width}",
            5000,
        )
        self.scan_coordinator.timer.log(f"emit {owner} acquire request")
        self.acquire_requested.emit(settings)
        return True

    def _request_automated_spectrum(self, owner: str) -> None:
        """Fail the requesting state machine if a frame cannot be queued."""

        if self._request_spectrum(owner):
            return
        if not self._instrument_connected("spectrometer"):
            message = "The spectrometer disconnected before the frame was queued."
        else:
            message = "The frame could not be queued because acquisition ownership changed."

        if owner == "power_scan":
            self.scan_coordinator.handle_acquisition_failed(message)
        elif owner == "gated":
            self.gated_coordinator.handle_acquisition_failed(message)
        elif owner == "auto_tune":
            self.auto_acquisition.handle_acquisition_failed(message)

    @Slot()
    def take_spectrum(self) -> None:
        self._request_spectrum("manual")

    @Slot(bool)
    def _on_live_changed(self, enabled: bool) -> None:
        self.live_next_timer.stop()
        if enabled:
            if not self._instrument_connected("spectrometer") or not self._claim_sequence(
                "live"
            ):
                self.acquisition_panel.set_live_enabled(False)
                return
            self._schedule_next_live_acquisition()
        elif not self.acquiring or self._acquisition_owner != "live":
            self._release_sequence("live")
        self.statusBar().showMessage(
            f"Live acquisition: {'ON' if enabled else 'OFF'}",
            5000,
        )

    def _schedule_next_live_acquisition(self) -> None:
        if not self.acquisition_panel.is_live_enabled() or self.acquiring:
            return
        if self.sequence_arbiter.owner != "live":
            return
        self.live_next_timer.start(max(0, int(self.display_settings.live_acquisition_gap_ms)))

    @Slot()
    def _start_live_acquisition_if_ready(self) -> None:
        if not self.acquisition_panel.is_live_enabled() or self.acquiring:
            return
        self._request_spectrum("live")

    @Slot(object)
    def _on_spectrum_ready(self, record: SpectrumRecord) -> None:
        if self._closing:
            return

        owner = self._acquisition_owner
        self._finish_acquisition_ui()

        self.performance_monitor.mark_acquisition()
        self.current_record = record
        self.file_io.set_current_record(record)
        self.spectrum_panel.queue_record(record)
        self._display_power_snapshot(record.p_after)

        if (
            (self.snr_settings.enabled or owner == "auto_tune")
            and record.snr is not None
        ):
            self.acquisition_panel.set_snr(record.snr)

        if self.power_monitor_settings.append_spectrum_power:
            self._append_power_history(
                record.mean_power_snapshot(),
                source="spectrum_mean",
            )

        self._check_signal_warning(record)
        if self.monitor_panel.tracking_enabled():
            self.monitor_panel.add_record(record)

        self.statusBar().showMessage(
            f"Spectrum acquired: {record.timestamp_utc}, mean ch1 power "
            f"{format_power_w(record.mean_power_w(0))}",
            10_000,
        )

        if owner == "auto_tune":
            self.auto_acquisition.handle_spectrum_ready(record)
        elif owner == "gated":
            self.gated_coordinator.handle_spectrum_ready(record)
        elif owner == "power_scan":
            self.scan_coordinator.handle_spectrum_ready(record)

        # Automated coordinators own their save policy. Manual and live frames
        # use the global autosave setting exactly once.
        if (
            self.file_name_settings.autosave_spectra
            and owner in {None, "manual", "live"}
        ):
            self._autosave_spectrum(record)

        if owner == "manual":
            self._release_sequence("manual")
        elif owner == "live" and self.acquisition_panel.is_live_enabled():
            self._schedule_next_live_acquisition()
        elif owner == "live":
            self._release_sequence("live")

    @Slot(str)
    def _on_acquisition_failed(self, message: str) -> None:
        owner = self._acquisition_owner
        self._finish_acquisition_ui()

        if owner == "power_scan":
            self.scan_coordinator.handle_acquisition_failed(message)
        elif owner == "auto_tune":
            self.auto_acquisition.handle_acquisition_failed(message)
        elif owner == "gated":
            self.gated_coordinator.handle_acquisition_failed(message)
        elif owner == "manual":
            self._release_sequence("manual")

        if owner == "live":
            self.acquisition_panel.set_live_enabled(False)
            self._release_sequence("live")

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
        self._acquisition_owner = None
        self.acquisition_panel.set_acquiring(False)
        self._refresh_sequence_controls()

    def capture_background(self) -> None:
        if not self._instrument_connected("spectrometer"):
            return
        if self.acquiring or not self._claim_sequence("background"):
            return
        self.acquiring = True
        self._acquisition_owner = "background"
        self._refresh_sequence_controls()
        settings = replace(self._settings(), subtract_background=False)
        self.statusBar().showMessage("Capturing background spectrum...", 5000)
        self.background_capture_requested.emit(settings)

    def _spectrometer_integration_limits_ms(
        self,
    ) -> tuple[int, int]:
        caps = getattr(
            self,
            "spectrometer_capabilities",
            None,
        )

        if caps is not None:
            minimum_us = int(
                caps.integration_time_min_us or 0
            )
            maximum_us = int(
                caps.integration_time_max_us or 0
            )

            if (
                minimum_us > 0
                and maximum_us >= minimum_us
            ):
                minimum_ms = max(
                    1,
                    math.ceil(minimum_us / 1000.0),
                )
                maximum_ms = max(
                    minimum_ms,
                    math.floor(maximum_us / 1000.0),
                )

                return minimum_ms, maximum_ms

        return (
            int(self.acquisition_panel.integration_ms.minimum()),
            int(self.acquisition_panel.integration_ms.maximum()),
        )

    def show_acquisition_recommendation(
        self,
    ) -> None:
        record = self.current_record

        if record is None or record.snr is None:
            QMessageBox.information(
                self,
                "No SNR estimate",
                "Acquire a spectrum with SNR estimation "
                "enabled before requesting a recommendation.",
            )
            return

        if not record.snr.valid:
            QMessageBox.information(
                self,
                "Invalid SNR estimate",
                record.snr.message,
            )
            return

        current = self.acquisition_panel.settings(
            run_identifier=(
                self.file_name_settings.run_identifier
            ),
            notes=self.file_name_settings.notes,
        )

        minimum_ms, maximum_ms = (
            self._spectrometer_integration_limits_ms()
        )

        maximum_ms = min(
            maximum_ms,
            int(
                self.snr_settings
                .maximum_integration_ms
            ),
        )

        suggestion = suggest_acquisition(
            result=record.snr,
            metric=(
                self.snr_settings
                .recommendation_metric
            ),
            current_integration_ms=(
                current.integration_ms
            ),
            current_averages=current.averages,
            target_snr=self.snr_settings.target_snr,
            target_peak_fraction=(
                self.snr_settings
                .target_peak_fraction
            ),
            minimum_integration_ms=minimum_ms,
            maximum_integration_ms=maximum_ms,
            maximum_averages=(
                self.snr_settings
                .maximum_averages
            ),
            maximum_total_acquisition_s=(
                self.snr_settings
                .maximum_total_acquisition_s
            ),
        )

        dialog = AcquisitionRecommendationDialog(
            current_integration_ms=(
                current.integration_ms
            ),
            current_averages=current.averages,
            suggestion=suggestion,
            parent=self,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.acquisition_panel.set_acquisition_parameters(
            integration_ms=suggestion.integration_ms,
            averages=suggestion.averages,
        )

        self.acquisition_panel.save_preferences(
            QSettings()
        )

        if (
            dialog.choice
            == RecommendationChoice
            .APPLY_AND_ACQUIRE
        ):
            QTimer.singleShot(
                0,
                self.take_spectrum,
            )

    def start_or_abort_auto_tune(self) -> None:
        if self.auto_acquisition.active:
            self.auto_acquisition.abort()
            return

        if not self._instrument_connected("spectrometer"):
            QMessageBox.information(
                self,
                "No Spectrometer",
                "Connect a spectrometer before starting automatic tuning.",
            )
            return

        if not self._begin_sequence("auto_tune"):
            return

        current = self._settings()
        minimum_ms, maximum_ms = self._spectrometer_integration_limits_ms()
        try:
            self.auto_acquisition.start(
                current_settings=current,
                snr_settings=self.snr_settings,
                minimum_integration_ms=minimum_ms,
                maximum_integration_ms=maximum_ms,
                # A prior frame may have been measured with different SNR
                # windows/metrics. Always verify the current settings with a
                # fresh acquisition after the worker receives its SNR config.
                initial_record=None,
            )
        except Exception as exc:
            self._release_sequence("auto_tune")
            QMessageBox.critical(self, "Auto Tune Failed", str(exc))
            return
        if not self.auto_acquisition.active:
            self._release_sequence("auto_tune")

    @Slot(bool)
    def _on_auto_tune_active_changed(self, active: bool) -> None:
        if active:
            self.sequence_arbiter.claim("auto_tune")
        else:
            self.sequence_arbiter.release("auto_tune")
        self.acquisition_panel.set_auto_tuning(active)
        self.acquisition_panel.set_snr_enabled(
            active or self.snr_settings.enabled
        )
        self._refresh_sequence_controls()

    @Slot(object)
    def _on_auto_tune_completed(self, result) -> None:
        self.acquisition_panel.set_acquisition_parameters(
            integration_ms=result.integration_ms,
            averages=result.averages,
        )
        self.acquisition_panel.save_preferences(QSettings())
        if result.limit_reached:
            QMessageBox.information(self, "Auto Tune Complete", result.message)

    def preview_gated_acquisition(self) -> None:
        try:
            plan = self.gated_coordinator.preview(self.gated_panel.settings())
        except Exception as exc:
            QMessageBox.critical(self, "Gated Preview Failed", str(exc))
            return
        self.gated_panel.set_plan(plan)

    def start_power_scan(self) -> None:
        if not self._instrument_connected("spectrometer") or not self._instrument_connected(
            "lasers"
        ):
            QMessageBox.information(
                self,
                "Instruments unavailable",
                "Connect the spectrometer and laser box before starting a power scan.",
            )
            return
        if not self._begin_sequence("power_scan"):
            return
        try:
            started = self.scan_coordinator.start_power_scan()
        except Exception as exc:
            self._release_sequence("power_scan")
            QMessageBox.critical(self, "Power Scan Failed", str(exc))
            return
        if not started:
            self._release_sequence("power_scan")

    def start_calibration_scan(self) -> None:
        if not all(
            self._instrument_connected(key)
            for key in ("power_meter", "lasers")
        ):
            QMessageBox.information(
                self,
                "Instruments unavailable",
                "Connect the power meter and laser box before running calibration.",
            )
            return
        if not self._begin_sequence("calibration"):
            return
        try:
            started = self.scan_coordinator.start_calibration_scan()
        except Exception as exc:
            self._release_sequence("calibration")
            QMessageBox.critical(self, "Calibration Failed", str(exc))
            return
        if not started:
            self._release_sequence("calibration")

    @Slot(bool, str)
    def _on_scan_active_changed(self, active: bool, owner: str) -> None:
        if active:
            self.sequence_arbiter.claim(owner)
        else:
            self.sequence_arbiter.release(owner)
        self._refresh_sequence_controls()

    def start_gated_acquisition(self) -> None:
        laser = self.laser_panel.selected_laser()
        if laser is None:
            QMessageBox.information(self, "No Laser", "Select a laser first.")
            return
        if not self._instrument_connected("spectrometer"):
            QMessageBox.information(self, "No Spectrometer", "Connect a spectrometer first.")
            return
        if not self._instrument_connected("lasers"):
            QMessageBox.information(self, "No Laser Box", "Connect a laser box first.")
            return
        if not self._begin_sequence("gated"):
            return
        try:
            plan = self.gated_coordinator.start(
                settings=self.gated_panel.settings(),
                laser=laser,
            )
        except Exception as exc:
            self._release_sequence("gated")
            QMessageBox.critical(self, "Gated Acquisition Failed", str(exc))
            return
        self.gated_panel.set_plan(plan)

    @Slot(bool)
    def _on_gated_active_changed(self, active: bool) -> None:
        if active:
            self.sequence_arbiter.claim("gated")
        else:
            self.sequence_arbiter.release("gated")
        self.gated_panel.set_running(active)
        self._refresh_sequence_controls()

    @Slot(str)
    def _on_gated_acquisition_failed(
        self,
        message: str,
    ) -> None:
        QMessageBox.warning(
            self,
            "Gated acquisition failed",
            str(message),
        )

    # ------------------------------------------------------------------- power

    @Slot(object)
    def _on_power_ready(self, power: PowerSnapshot) -> None:
        if self._closing:
            return
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
        if self.acquiring or self.sequence_arbiter.automated:
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
        if not self._instrument_connected("power_meter"):
            return
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
        if self._closing:
            return
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
        self.statusBar().showMessage(
            "No spectrometer or power meter connected. "
            "The GUI is running in offline mode.",
            15_000,
        )

        print(message, file=sys.stderr)

    @Slot(str)
    def _connect_instrument(self, key: str) -> None:
        if self.acquiring or self.sequence_arbiter.active:
            QMessageBox.information(
                self,
                "Instrument busy",
                "Stop the active acquisition sequence before changing connections.",
            )
            return
        if key == "spectrometer":
            self.runtime.connect_spectrometer()
        elif key == "power_meter":
            self.runtime.connect_power_meter()
        elif key == "lasers":
            self.runtime.refresh_lasers()

    @Slot(str)
    def _disconnect_instrument(
        self,
        key: str,
    ) -> None:
        if self.acquiring or self.sequence_arbiter.active:
            QMessageBox.information(
                self,
                "Instrument busy",
                "Stop the active acquisition sequence before changing connections.",
            )
            return
        if key == "spectrometer":
            self.runtime.disconnect_spectrometer()
            return
        elif key == "power_meter":
            self.runtime.disconnect_power_meter()
        elif key == "lasers":
            self.runtime.disconnect_lasers()

    @Slot()
    def _reconnect_all_instruments(self) -> None:
        if self.acquiring or self.sequence_arbiter.active:
            QMessageBox.information(
                self,
                "Instrument busy",
                "Stop the active acquisition sequence before reconnecting instruments.",
            )
            return
        self.runtime.connect_spectrometer()
        self.runtime.connect_power_meter()
        self.runtime.refresh_lasers()

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        self.statusBar().showMessage(
            "Instrument worker error. See console output.",
            10_000,
        )
        print(message, file=sys.stderr)

    @Slot(str)
    def _on_laser_error(self, message: str) -> None:
        print(message, file=sys.stderr)
        self.statusBar().showMessage(
            "Laser controller error. See console output.",
            10_000,
        )

    @Slot()
    def _on_disable_all_lasers_requested(self) -> None:
        """Stop laser-owning state machines before issuing the emergency command."""

        owner = self.sequence_arbiter.owner
        if owner in {"power_scan", "calibration"}:
            self.scan_coordinator.abort_power_scan()
        elif owner == "gated":
            self.gated_coordinator.abort()
        elif owner == "auto_tune":
            self.auto_acquisition.abort()

        # Send this after coordinator abort requests so it is the final queued
        # laser command even when an abort also emits a channel-specific disable.
        self.runtime.disable_all_lasers()
        self.statusBar().showMessage(
            "Disable All requested; automated laser work is stopping.",
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
        self._release_sequence("background")
        self.statusBar().showMessage(
            f"Background captured: {background.timestamp_utc}, "
            f"{background.integration_ms} ms, avg={background.averages}",
            15_000,
        )

    @Slot()
    def _on_background_cleared(self) -> None:
        self.statusBar().showMessage("Background spectrum cleared.", 10_000)

    @Slot(str)
    def _on_background_failed(self, message: str) -> None:
        self._finish_acquisition_ui()
        self._release_sequence("background")
        self.statusBar().showMessage("Background acquisition failed.", 15_000)
        print(message, file=sys.stderr)
        QMessageBox.warning(
            self,
            "Background Acquisition Failed",
            message.splitlines()[-1] if message else "Unknown error.",
        )

    @Slot(float)
    def _on_spectrometer_temperature_ready(self, temperature_c: float) -> None:
        self.statusBar().showMessage(
            f"CCD temperature: {float(temperature_c):.2f} °C",
            10_000,
        )

    @Slot()
    def _on_monitor_cleared(self) -> None:
        self.statusBar().showMessage("Spectrum monitor cleared.", 5000)

    @Slot(object)
    def _on_instrument_connection_changed(
        self,
        state: InstrumentConnectionState,
    ) -> None:
        self.instrument_states[state.key] = state
        self._apply_instrument_visibility()

        dialog = getattr(
            self,
            "_instrument_connections_dialog",
            None,
        )

        if dialog is not None:
            dialog.set_state(state)

        QTimer.singleShot(
            0,
            lambda: clamp_main_window_to_available_screen(self),
        )

    def _instrument_connected(
        self,
        key: str,
    ) -> bool:
        state = self.instrument_states.get(key)
        return bool(state and state.connected)

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

    def _apply_instrument_visibility(self) -> None:
        spectrometer = self._instrument_connected(
            "spectrometer"
        )
        power_meter = self._instrument_connected(
            "power_meter"
        )
        lasers = self._instrument_connected(
            "lasers"
        )

        # Spectrometer controls.
        self.acquisition_panel.setVisible(spectrometer)
        self.tabs.setTabVisible(
            self.monitor_tab_index,
            spectrometer,
        )

        if not spectrometer:
            self.acquisition_panel.set_live_enabled(
                False
            )
            self.live_next_timer.stop()
            self.spectrometer_capabilities = None

        # Keep Spectrum tab visible for offline file viewing.
        self.tabs.setTabVisible(
            self.spectrum_tab_index,
            True,
        )

        # Newport controls.
        self.power_dock.setVisible(power_meter)
        self.power_label.setVisible(power_meter)
        self.power_label_action.setVisible(power_meter)

        if not power_meter:
            self.power_timer.stop()
            self.last_power_meter_wavelength_nm = None
            self.power_label.setText("")
        else:
            self._apply_power_monitor_settings()
            self.power_label.setText("Power: --")

        # OBIS controls.
        self.lower_tabs.setTabVisible(
            self.laser_tab_index,
            lasers,
        )
        self.lower_tabs.setTabVisible(
            self.scan_tab_index,
            lasers,
        )

        # Filter definitions remain editable offline.
        self.lower_tabs.setTabVisible(
            self.filter_tab_index,
            True,
        )

        self.gated_panel.set_instrument_availability(
            spectrometer_available=spectrometer,
            lasers_available=lasers,
        )

        self.lower_tabs.setTabVisible(
            self.gated_tab_index,
            spectrometer and lasers
        )

        # Use your actual button names here.
        self.scan_panel.set_instrument_availability(
            spectrometer_available=spectrometer,
            power_meter_available=power_meter,
            lasers_available=lasers,
        )
        self._refresh_sequence_controls()

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
            self.theme_manager,
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

    def show_instrument_connections_dialog(
        self,
    ) -> None:
        dialog = getattr(
            self,
            "_instrument_connections_dialog",
            None,
        )

        if dialog is None:
            dialog = InstrumentConnectionsDialog(
                self
            )

            dialog.connect_requested.connect(
                self._connect_instrument
            )

            dialog.disconnect_requested.connect(
                self._disconnect_instrument
            )

            dialog.reconnect_all_requested.connect(
                self._reconnect_all_instruments
            )

            self._instrument_connections_dialog = (
                dialog
            )

        for state in self.instrument_states.values():
            dialog.set_state(state)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

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
        if self.acquiring or self.sequence_arbiter.active:
            QMessageBox.information(
                self,
                "Instrument busy",
                "Stop the active instrument operation before querying or changing "
                "spectrometer settings.",
            )
            return
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

    def show_theme_editor_dialog(self) -> None:
        dialog = ThemeEditorDialog(
            self.theme_manager,
            base_theme=self.display_settings.theme_name,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.saved_theme is None:
            return
        self.display_settings.theme_name = dialog.saved_theme.key
        self._save_preferences()
        self.request_application_restart()

    def show_theme_preview_dialog(self) -> None:
        dialog = ThemePreviewDialog(
            self.theme_manager,
            current_theme=self.display_settings.theme_name,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.display_settings.theme_name = dialog.selected_theme
        self._save_preferences()
        self.request_application_restart()

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
        if self.acquiring or self.sequence_arbiter.active:
            QMessageBox.information(
                self,
                "Sequence active",
                "Stop the active acquisition sequence before restarting the GUI.",
            )
            return

        self._application_exit_code = RESTART_EXIT_CODE
        self.close()

    def closeEvent(self, event) -> None:
        if self._closing:
            event.accept()
            return

        if self.acquiring or self.sequence_arbiter.active:
            result = QMessageBox.question(
                self,
                "Acquisition still active",
                "An instrument operation is still active. Exit, stop scheduling new work, "
                "and send Disable All to the laser boxes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._closing = True

        # Prevent new work from being scheduled
        self.acquisition_panel.set_live_enabled(False)
        self.live_next_timer.stop()
        self.power_timer.stop()

        if self._instrument_connected("lasers"):
            self.runtime.disable_all_lasers()

        # Persist UI state
        self._save_preferences()
        self._save_window_layout()

        # Stop hardware workers
        self.runtime.shutdown()

        # Flush and close any remaining file/log resources.
        self.file_io.close()

        event.accept()

        exit_code = int(self._application_exit_code)
        app = QApplication.instance()

        if app is not None:
            QTimer.singleShot(
                0,
                lambda code=exit_code: app.exit(code),
            )
