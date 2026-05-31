from __future__ import annotations

import csv
import math
import sys
import time
from dataclasses import replace
from pathlib import Path
import numpy as np

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, Signal, Slot, QMetaObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QWidget,
)

import pyqtgraph as pg

from controllers.laser_controller import LaserController
from dialogs.power_details_dialog import PowerDetailsDialog
from dialogs.spectrometer_details_dialog import SpectrometerDetailsDialog
from panels.laser_panel import LaserPanel
from panels.power_panel import PowerPanel
from panels.acquisition_panel import AcquisitionPanel
from panels.monitor_panel import MonitorPanel
from panels.scan_panel import ScanPanel
from panels.filter_wheels_panel import FilterWheelPanel
from dialogs.settings_dialog import AppSettingsDialog
from controllers.device_controller import DeviceController
from io_utils.power_logging import FullPowerLogger
from io_utils.file_naming import build_power_trace_path, build_spectrum_path
from io_utils.spectrum_io import save_spectrum_record, load_spectrum_csv
from io_utils.calibration_io import save_calibration_csv, load_calibration_csv
from planning.power_scan import CalibrationCurve
from planning.filter_planning import (
    enumerate_filter_states,
    plan_min_filter_changes
)
from core.units import format_power_w
from core.time_utils import utc_now_iso
from core.laser_models import LaserChannelInfo, PowerScanPoint
from core.settings import (
    AcquisitionSettings, 
    DeviceConfig, 
    FileNameSettings, 
    PlotStyleSettings,
    PowerMonitorSettings, 
    SignalWarningSettings, 
)
from core.records import BackgroundSpectrum, PowerSnapshot, PowerTracePoint, SpectrometerInfo, SpectrumRecord, SpectrometerCapabilities
from core.preferences import get_bool, get_float, get_int, get_path, get_str

ESTIMATED_MONITOR_POINT_BYTES = 768

def qsettings_bool(settings: QSettings, key: str, default: bool) -> bool:
    value = settings.value(key, default)

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}

    return bool(value)


def qsettings_int(settings: QSettings, key: str, default: int) -> int:
    try:
        return int(settings.value(key, default))
    except Exception:
        return int(default)


def qsettings_float(settings: QSettings, key: str, default: float) -> float:
    try:
        return float(settings.value(key, default))
    except Exception:
        return float(default)


def qsettings_str(settings: QSettings, key: str, default: str) -> str:
    value = settings.value(key, default)
    return str(value)


class MainWindow(QMainWindow):
    acquire_requested = Signal(object)
    power_poll_requested = Signal()
    shutdown_requested = Signal()
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

    def __init__(self, config: DeviceConfig) -> None:
        super().__init__()

        self.setWindowTitle("Magneto-PL Spectrum Acquisition")
        self.resize(1600, 850)

        self.config = config

        # Load 
        self.file_name_settings = FileNameSettings()
        self.power_monitor_settings = PowerMonitorSettings()
        self.signal_warning_settings = SignalWarningSettings()
        self.plot_style_settings = PlotStyleSettings()
        self.spectrometer_info = SpectrometerInfo()

        self.full_power_logger: FullPowerLogger | None = None
        self.auto_update_power_meter_wavelength = True
        self.last_power_meter_wavelength_nm: int | None = None

        self.power_scan_active = False
        self.power_scan_abort_requested = False
        self.power_scan_points = []
        self.power_scan_laser = None
        self.power_scan_point_index = 0
        self.power_scan_repeat_index = 0

        self.current_laser_calibration: CalibrationCurve | None = None
        self.current_scan_filter_state = None

        self.calibration_active = False
        self.calibration_points = []
        self.calibration_laser = None
        self.calibration_index = 0
        self.calibration_read_index = 0
        self.calibration_readings_w = []
        self.calibration_results = []

        self.app_t0: float = time.perf_counter()
        self.acquiring: bool = False
        self.current_record: SpectrumRecord | None = None

        self.monitor_memory_warning_mb = 50.0
        self.monitor_memory_warning_issued = False

        self.last_signal_warning_s = -1.0e99

        self._build_plots()
        self._build_menus()
        self._build_toolbars()
        self._build_left_dock()
        self._build_power_dock()
        self._start_laser_controller()
        
        self.live_timer = QTimer(self)
        self.live_timer.timeout.connect(self._live_tick)
        self.live_timer.start(250)

        self.power_timer = QTimer(self)
        self.power_timer.timeout.connect(self._poll_power_tick)
        self._apply_power_monitor_settings()

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Starting device controller...")

        self.autosave_label = QLabel()
        self.statusBar().addPermanentWidget(self.autosave_label)
        self._update_autosave_indicator()

        self.controller_thread = QThread(self)
        self.controller = DeviceController(config)
        self.controller.moveToThread(self.controller_thread)
        self.controller_thread.start()
        self.controller_thread.started.connect(self.controller.connect_devices)

        self.acquire_requested.connect(self.controller.acquire)
        self.power_poll_requested.connect(self.controller.poll_power)
        self.shutdown_requested.connect(self.controller.shutdown)
        self.power_settings_changed.connect(self.controller.set_power_monitor_settings, Qt.ConnectionType.QueuedConnection)
        self.power_meter_wavelength_requested.connect(self.controller.set_power_meter_wavelength_nm, Qt.ConnectionType.QueuedConnection)
        self.laser_set_power_requested.connect(self.laser_controller.set_power_w, Qt.ConnectionType.QueuedConnection)
        self.laser_set_enabled_requested.connect(self.laser_controller.set_enabled, Qt.ConnectionType.QueuedConnection)
        self.power_read_once_requested.connect(self.controller.read_power_once, Qt.ConnectionType.QueuedConnection)
        self.background_capture_requested.connect(
            self.controller.capture_background,
            Qt.ConnectionType.QueuedConnection,
        )

        self.background_clear_requested.connect(
            self.controller.clear_background,
            Qt.ConnectionType.QueuedConnection,
        )

        self.tec_target_requested.connect(
            self.controller.set_tec_target_c,
            Qt.ConnectionType.QueuedConnection,
        )

        self.tec_enabled_requested.connect(
            self.controller.set_tec_enabled,
            Qt.ConnectionType.QueuedConnection,
        )

        self.spectrometer_temperature_requested.connect(
            self.controller.query_spectrometer_temperature,
            Qt.ConnectionType.QueuedConnection,
        )

        self.controller.connected.connect(self._on_connected)
        self.controller.connection_failed.connect(self._on_connection_failed)
        self.controller.spectrum_ready.connect(self._on_spectrum_ready)
        self.controller.power_ready.connect(self._on_power_ready)
        self.controller.power_meter_wavelength_ready.connect(self._on_power_meter_wavelength_ready, Qt.ConnectionType.QueuedConnection)
        self.controller.error.connect(self._on_worker_error)
        self.controller.spectrometer_info_ready.connect(self._on_spectrometer_info)
        self.controller.status.connect(self._show_status_message, Qt.ConnectionType.QueuedConnection)
        self.controller.power_read_complete.connect(self._on_power_read_once_complete, Qt.ConnectionType.QueuedConnection)
        self.controller.background_ready.connect(
            self._on_background_ready,
            Qt.ConnectionType.QueuedConnection,
        )

        self.controller.background_cleared.connect(
            self._on_background_cleared,
            Qt.ConnectionType.QueuedConnection,
        )

        self.controller.spectrometer_temperature_ready.connect(
            self._on_spectrometer_temperature_ready,
            Qt.ConnectionType.QueuedConnection,
        )

        self.scan_panel.calibration_requested.connect(self.start_calibration_scan)

        self._load_preferences()
        self._apply_plot_style()
        self._apply_loaded_preferences_to_ui()
        self._restore_window_layout()

        QTimer.singleShot(0, self._apply_initial_layout)

    def _build_plots(self) -> None:
        pg.setConfigOptions(antialias=True)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.spectrum_plot = pg.PlotWidget()
        self.spectrum_plot.setLabel("bottom", "Wavelength (nm)")
        self.spectrum_plot.setLabel("left", "Intensity (counts)")

        for axis_name in ["bottom", "left"]:
            axis = self.spectrum_plot.getAxis(axis_name)
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)

        self.spectrum_curve = self.spectrum_plot.plot()

        self.monitor_panel = MonitorPanel(parent=self)
        self.monitor_panel.set_application_t0(self.app_t0)
        self.monitor_panel.save_requested.connect(self.save_monitor_track)
        self.monitor_panel.cleared.connect(self._on_monitor_cleared)
        self.monitor_panel.memory_warning_requested.connect(self._on_monitor_memory_warning)

        self.tabs.addTab(self.spectrum_plot, "Spectrum")
        self.tabs.addTab(self.monitor_panel, "Monitor")

    def _build_menus(self) -> None:
        menu_file = self.menuBar().addMenu("&File")

        self.open_action = QAction("Open Spectrum...", self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(self.open_spectrum)

        self.save_action = QAction("Save Spectrum...", self)
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.triggered.connect(self.save_spectrum)

        self.save_track_action = QAction("Save Monitor Track...", self)
        self.save_track_action.triggered.connect(self.save_monitor_track)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)

        menu_file.addAction(self.open_action)
        menu_file.addAction(self.save_action)
        menu_file.addAction(self.save_track_action)
        menu_file.addSeparator()
        menu_file.addAction(quit_action)

        self.menuBar().addMenu("&Edit")
        self.menuBar().addMenu("&View")
        
        menu_tool = self.menuBar().addMenu("&Tools")
        
        self.toggle_live_action = QAction("Toggle Live", self)
        self.toggle_live_action.setShortcut("Ctrl+L")
        self.toggle_live_action.triggered.connect(self.toggle_live)
        menu_tool.addAction(self.toggle_live_action)
        
        self.clear_power_action = QAction("Clear Power Trace", self)
        self.clear_power_action.triggered.connect(self.clear_power_trace)
        menu_tool.addAction(self.clear_power_action)
        
        self.clear_all_action = QAction("Clear All Monitors", self)
        self.clear_all_action.setShortcut("Ctrl+Shift+C")
        self.clear_all_action.triggered.connect(self.clear_all_monitors)
        menu_tool.addAction(self.clear_all_action)
        
        self.power_details_action = QAction("Power Trace Details...", self)
        self.power_details_action.triggered.connect(self.show_power_details_dialog)
        menu_tool.addAction(self.power_details_action)
    
        self.start_power_log_action = QAction("Start Full Power Log...", self)
        self.start_power_log_action.triggered.connect(self.start_full_power_log)
        menu_tool.addAction(self.start_power_log_action)

        self.stop_power_log_action = QAction("Stop Full Power Log", self)
        self.stop_power_log_action.triggered.connect(self.stop_full_power_log)
        self.stop_power_log_action.setEnabled(False)
        menu_tool.addAction(self.stop_power_log_action)

        self.spectrometer_details_action = QAction("Spectrometer Details...", self)
        self.spectrometer_details_action.triggered.connect(self.show_spectrometer_details_dialog)
        menu_tool.addAction(self.spectrometer_details_action)

        self.menuBar().addMenu("&Help")

        settings_menu = self.menuBar().addMenu("&Settings")
        clear_track_action = QAction("Clear Monitor Track", self)
        clear_track_action.triggered.connect(self.clear_monitor_track)
        settings_menu.addAction(clear_track_action)

        settings_action = QAction("Acquisition Settings...", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        settings_menu.addAction(settings_action)

        self.save_power_trace_action = QAction("Save Power Trace...", self)
        self.save_power_trace_action.triggered.connect(self.save_power_trace)
        menu_file.addAction(self.save_power_trace_action)

    def _build_toolbars(self) -> None:
        toolbar = QToolBar("Acquisition")
        toolbar.setObjectName("acquisition_toolbar")
        toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        self.acquisition_toolbar = toolbar

        self.acquire_action = QAction("Take Spectrum", self)
        self.acquire_action.setShortcut("F5")
        self.acquire_action.setStatusTip("Take Spectrum (F5)")
        self.acquire_action.triggered.connect(self.take_spectrum)

        toolbar.addAction(self.acquire_action)
        toolbar.addAction(self.save_action)
        toolbar.addAction(self.open_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.power_label = QLabel("Power: --")
        self.power_label.setMinimumWidth(260)
        self.power_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        toolbar.addWidget(self.power_label)

    def _build_left_dock(self) -> None:
        dock = QDockWidget("Controls", self)
        dock.setObjectName("controls_dock")
        dock.setMinimumWidth(285)
        self.controls_dock = dock

        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        dock.setMinimumWidth(330)

        self.acquisition_panel = AcquisitionPanel(self)
        self.acquisition_panel.acquire_requested.connect(self.take_spectrum)

        self.acquisition_panel.background_requested.connect(self.capture_background)
        self.acquisition_panel.background_clear_requested.connect(
            self.background_clear_requested.emit
        )

        self.laser_panel = LaserPanel(self)
        self.scan_panel = ScanPanel(self)
        self.filter_wheel_panel = FilterWheelPanel(self)

        self.scan_panel.preview_requested.connect(self.preview_power_scan)
        self.scan_panel.run_requested.connect(self.start_power_scan)
        self.scan_panel.abort_requested.connect(self.abort_power_scan)
        self.scan_panel.save_calibration_requested.connect(self.save_current_calibration)
        self.scan_panel.load_calibration_requested.connect(self.load_calibration)

        laser_tabs = QTabWidget()
        laser_tabs.addTab(self.laser_panel, "Lasers")
        laser_tabs.addTab(self.scan_panel, "Scan")
        laser_tabs.addTab(self.filter_wheel_panel, "Filters")

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("controls_splitter")
        splitter.addWidget(self.acquisition_panel)
        splitter.addWidget(laser_tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        dock.setWidget(splitter)

    def _build_power_dock(self) -> None:
        dock = QDockWidget("Power", self)
        dock.setObjectName("power_dock")
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        dock.setMinimumWidth(180)

        self.power_panel = PowerPanel(
            max_points=int(self.power_monitor_settings.max_points),
            parent=self,
        )

        self.power_panel.clear_requested.connect(self.clear_power_trace)
        self.power_panel.save_requested.connect(self.save_power_trace)
        self.power_panel.details_requested.connect(self.show_power_details_dialog)
        self.power_panel.auto_wavelength_changed.connect(self._on_auto_power_meter_wavelength_changed)
        self.power_panel.mode_changed.connect(self._on_power_monitor_mode_changed)

        dock.setWidget(self.power_panel)

        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.power_dock = dock

    def _apply_initial_layout(self) -> None:
        """
        Initial horizontal layout.

        With a ~1300 px window:
        left controls ≈ 260 px
        right power dock ≈ 250 px
        central spectrum ≈ 750-800 px

        That makes the spectrum view roughly 3x the power monitor width.
        The user can still resize docks manually afterward.
        """

        try:
            self.resizeDocks(
                [self.controls_dock, self.power_dock],
                [315, 210],
                Qt.Orientation.Horizontal,
            )
        except Exception:
            pass

    def _apply_loaded_preferences_to_ui(self) -> None:
        self.power_panel.set_mode(self.power_monitor_settings.mode)
        self.power_panel.set_auto_wavelength_enabled(self.auto_update_power_meter_wavelength)

        self._apply_power_monitor_settings()
        self._apply_plot_style()
        self._update_autosave_indicator()

    def _apply_plot_style(self) -> None:
        s = self.plot_style_settings

        spectrum_pen = (
            pg.mkPen(s.spectrum_color, width=s.spectrum_line_width)
            if s.spectrum_show_line and s.spectrum_line_width > 0
            else None
        )

        spectrum_symbol = s.symbol if s.spectrum_show_symbols else None

        self.spectrum_curve.setPen(spectrum_pen)
        self.spectrum_curve.setSymbol(spectrum_symbol)
        self.spectrum_curve.setSymbolSize(int(s.symbol_size))

        font = self.font()
        font.setPointSize(int(s.font_size))

        for axis_name in ["bottom", "left"]:
            axis = self.spectrum_plot.getAxis(axis_name)
            axis.setTickFont(font)

            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)

        if s.spectrum_auto_range:
            self.spectrum_plot.enableAutoRange()
        else:
            if s.spectrum_x_max > s.spectrum_x_min:
                self.spectrum_plot.setXRange(
                    s.spectrum_x_min,
                    s.spectrum_x_max,
                    padding=0.0,
                )

            if s.spectrum_y_max > s.spectrum_y_min:
                self.spectrum_plot.setYRange(
                    s.spectrum_y_min,
                    s.spectrum_y_max,
                    padding=0.0,
                )

        self.monitor_panel.apply_plot_style(s)
        self.power_panel.apply_plot_style(s)

    def _curve_style(self, *, color: str, width: float, show_line: bool, show_symbols: bool):
        pen = pg.mkPen(color, width=width) if show_line and width > 0 else None
        symbol = self.plot_style_settings.symbol if show_symbols else None
        symbol_size = int(self.plot_style_settings.symbol_size)

        return pen, symbol, symbol_size

    def _settings(self) -> AcquisitionSettings:
        settings = self.acquisition_panel.settings(
            run_identifier=self.file_name_settings.run_identifier,
            notes=self.file_name_settings.notes,
        )

        if self.power_scan_active and self.power_scan_laser is not None:
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

    def _load_preferences(self) -> None:
        settings = QSettings()

        # File naming.
        self.file_name_settings.save_directory = get_path(
            settings,
            "files/save_directory",
            self.file_name_settings.save_directory,
        )
        self.file_name_settings.base_name = get_str(
            settings,
            "files/base_name",
            self.file_name_settings.base_name,
        )
        self.file_name_settings.run_identifier = get_str(
            settings,
            "files/run_identifier",
            self.file_name_settings.run_identifier,
        )
        self.file_name_settings.notes = get_str(
            settings,
            "files/notes",
            self.file_name_settings.notes,
        )
        self.file_name_settings.include_date = get_bool(
            settings,
            "files/include_date",
            self.file_name_settings.include_date,
        )
        self.file_name_settings.include_time = get_bool(
            settings,
            "files/include_time",
            self.file_name_settings.include_time,
        )
        self.file_name_settings.include_power = get_bool(
            settings,
            "files/include_power",
            self.file_name_settings.include_power,
        )
        self.file_name_settings.include_field = get_bool(
            settings,
            "files/include_field",
            self.file_name_settings.include_field,
        )
        self.file_name_settings.include_run_identifier = get_bool(
            settings,
            "files/include_run_identifier",
            self.file_name_settings.include_run_identifier,
        )
        self.file_name_settings.include_enumeration = get_bool(
            settings,
            "files/include_enumeration",
            self.file_name_settings.include_enumeration,
        )
        self.file_name_settings.autosave_spectra = get_bool(
            settings,
            "files/autosave_spectra",
            self.file_name_settings.autosave_spectra,
        )

        # Power monitor.
        self.power_monitor_settings.mode = get_str(
            settings,
            "power/mode",
            self.power_monitor_settings.mode,
        )
        if self.power_monitor_settings.mode not in {"live", "spectra_only"}:
            self.power_monitor_settings.mode = "live"

        self.power_monitor_settings.polling_enabled = (
            self.power_monitor_settings.mode == "live"
        )
        self.power_monitor_settings.append_spectrum_power = get_bool(
            settings,
            "power/append_spectrum_power",
            self.power_monitor_settings.append_spectrum_power,
        )
        self.power_monitor_settings.max_points = get_int(
            settings,
            "power/max_points",
            self.power_monitor_settings.max_points,
        )
        self.power_monitor_settings.interval_ms = get_int(
            settings,
            "power/interval_ms",
            self.power_monitor_settings.interval_ms,
        )
        self.power_monitor_settings.validation_enabled = get_bool(
            settings,
            "power/validation_enabled",
            self.power_monitor_settings.validation_enabled,
        )
        self.power_monitor_settings.max_valid_power_w = get_float(
            settings,
            "power/max_valid_power_w",
            self.power_monitor_settings.max_valid_power_w,
        )
        self.power_monitor_settings.invalid_power_retries = get_int(
            settings,
            "power/invalid_power_retries",
            self.power_monitor_settings.invalid_power_retries,
        )
        self.power_monitor_settings.invalid_power_retry_delay_s = get_float(
            settings,
            "power/invalid_power_retry_delay_s",
            self.power_monitor_settings.invalid_power_retry_delay_s,
        )

        # Signal warning.
        self.signal_warning_settings.enabled = get_bool(
            settings,
            "warnings/enabled",
            self.signal_warning_settings.enabled,
        )
        self.signal_warning_settings.use_spectrometer_max = get_bool(
            settings,
            "warnings/use_spectrometer_max",
            self.signal_warning_settings.use_spectrometer_max,
        )
        self.signal_warning_settings.fraction_of_spectrometer_max = get_float(
            settings,
            "warnings/fraction_of_spectrometer_max",
            self.signal_warning_settings.fraction_of_spectrometer_max,
        )
        self.signal_warning_settings.absolute_threshold_counts = get_float(
            settings,
            "warnings/absolute_threshold_counts",
            self.signal_warning_settings.absolute_threshold_counts,
        )
        self.signal_warning_settings.popup_enabled = get_bool(
            settings,
            "warnings/popup_enabled",
            self.signal_warning_settings.popup_enabled,
        )
        self.signal_warning_settings.popup_cooldown_s = get_float(
            settings,
            "warnings/popup_cooldown_s",
            self.signal_warning_settings.popup_cooldown_s,
        )

        # Plot style.
        self.plot_style_settings.spectrum_color = get_str(
            settings,
            "plot/spectrum_color",
            self.plot_style_settings.spectrum_color,
        )
        self.plot_style_settings.monitor_color = get_str(
            settings,
            "plot/monitor_color",
            self.plot_style_settings.monitor_color,
        )
        self.plot_style_settings.power_color = get_str(
            settings,
            "plot/power_color",
            self.plot_style_settings.power_color,
        )
        self.plot_style_settings.spectrum_line_width = get_float(
            settings,
            "plot/spectrum_line_width",
            self.plot_style_settings.spectrum_line_width,
        )
        self.plot_style_settings.monitor_line_width = get_float(
            settings,
            "plot/monitor_line_width",
            self.plot_style_settings.monitor_line_width,
        )
        self.plot_style_settings.power_line_width = get_float(
            settings,
            "plot/power_line_width",
            self.plot_style_settings.power_line_width,
        )
        self.plot_style_settings.spectrum_show_line = get_bool(
            settings,
            "plot/spectrum_show_line",
            self.plot_style_settings.spectrum_show_line,
        )
        self.plot_style_settings.monitor_show_line = get_bool(
            settings,
            "plot/monitor_show_line",
            self.plot_style_settings.monitor_show_line,
        )
        self.plot_style_settings.power_show_line = get_bool(
            settings,
            "plot/power_show_line",
            self.plot_style_settings.power_show_line,
        )
        self.plot_style_settings.spectrum_show_symbols = get_bool(
            settings,
            "plot/spectrum_show_symbols",
            self.plot_style_settings.spectrum_show_symbols,
        )
        self.plot_style_settings.monitor_show_symbols = get_bool(
            settings,
            "plot/monitor_show_symbols",
            self.plot_style_settings.monitor_show_symbols,
        )
        self.plot_style_settings.power_show_symbols = get_bool(
            settings,
            "plot/power_show_symbols",
            self.plot_style_settings.power_show_symbols,
        )
        self.plot_style_settings.symbol = get_str(
            settings,
            "plot/symbol",
            self.plot_style_settings.symbol,
        )
        self.plot_style_settings.symbol_size = get_int(
            settings,
            "plot/symbol_size",
            self.plot_style_settings.symbol_size,
        )
        self.plot_style_settings.font_size = get_int(
            settings,
            "plot/font_size",
            self.plot_style_settings.font_size,
        )
        self.plot_style_settings.spectrum_auto_range = get_bool(
            settings,
            "plot/spectrum_auto_range",
            self.plot_style_settings.spectrum_auto_range,
        )
        self.plot_style_settings.spectrum_x_min = get_float(
            settings,
            "plot/spectrum_x_min",
            self.plot_style_settings.spectrum_x_min,
        )
        self.plot_style_settings.spectrum_x_max = get_float(
            settings,
            "plot/spectrum_x_max",
            self.plot_style_settings.spectrum_x_max,
        )
        self.plot_style_settings.spectrum_y_min = get_float(
            settings,
            "plot/spectrum_y_min",
            self.plot_style_settings.spectrum_y_min,
        )
        self.plot_style_settings.spectrum_y_max = get_float(
            settings,
            "plot/spectrum_y_max",
            self.plot_style_settings.spectrum_y_max,
        )

        # Filter wheel
        filter_json = get_str(
            settings,
            "filters/config_json",
            ""
        )

        if filter_json:
            try:
                self.filter_wheel_panel.deserialize(filter_json)
            except Exception:
                pass

        # Panel-level widget settings.
        self.acquisition_panel.load_preferences(settings)
        self.monitor_panel.load_preferences(settings)
        self.power_panel.load_preferences(settings)
        self.laser_panel.load_preferences(settings)
        self.scan_panel.load_preferences(settings)

        self.auto_update_power_meter_wavelength = self.power_panel.auto_wavelength_enabled()

    def _restore_window_layout(self) -> None:
        settings = QSettings()

        geometry = settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        state = settings.value("window/state")
        if state is not None:
            self.restoreState(state)

    def _save_window_layout(self) -> None:
        settings = QSettings()
        settings.setValue("window/geometry", self.saveGeometry())
        settings.setValue("window/state", self.saveState())
        settings.sync()

    def take_spectrum(self) -> None:
        if self.acquiring:
            return

        self.acquiring = True
        self.acquire_action.setEnabled(False)
        self.acquisition_panel.set_acquiring(True)

        settings = self._settings()

        self.statusBar().showMessage(
            (
                f"Acquiring spectrum: "
                f"{settings.integration_ms} ms, "
                f"avg={settings.averages}, "
                f"boxcar={settings.boxcar_width}"
            ),
            5000,
        )

        self.acquire_requested.emit(settings)

    @Slot()
    def _live_tick(self) -> None:
        if self.acquisition_panel.is_live_enabled() and not self.acquiring:
            self.take_spectrum()

    @Slot(str)
    def _on_connected(self, message: str) -> None:
        self.statusBar().showMessage(message, 10000)

    @Slot(str)
    def _on_connection_failed(self, message: str) -> None:
        self.statusBar().showMessage("Device connection failed.")
        QMessageBox.critical(self, "Device connection failed", message)

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        self.acquiring = False
        self.acquire_action.setEnabled(True)
        self.statusBar().showMessage("Worker error. See console output.")
        print(message, file=sys.stderr)

    @Slot(object)
    def _on_power_ready(self, power: PowerSnapshot) -> None:
        if (
            not self.power_monitor_settings.polling_enabled
            or self.power_monitor_settings.mode != "live"
        ):
            return

        self.power_panel.set_current_power(power)
        self._append_power_history(power, source="poll")

    @Slot(object)
    def _on_spectrum_ready(self, record: SpectrumRecord) -> None:
        self.acquiring = False
        self.acquire_action.setEnabled(True)
        self.acquisition_panel.set_acquiring(False)

        self.current_record = record

        self.spectrum_curve.setData(
            record.wavelengths_nm,
            record.intensities_counts,
        )

        self.power_panel.set_current_power(record.p_after)

        if self.power_monitor_settings.append_spectrum_power:
            mean_power = self._mean_spectrum_power_snapshot(record)
            self._append_power_history(mean_power, source="spectrum_mean")

        self._check_signal_warning(record)

        if self.monitor_panel.tracking_enabled():
            self.monitor_panel.add_record(record)

        if self.file_name_settings.autosave_spectra and not self.power_scan_active:
            self._autosave_spectrum(record)

        p_mean = record.mean_power_w(0)

        self.statusBar().showMessage(
            f"Spectrum acquired: {record.timestamp_utc}, mean ch1 power {format_power_w(p_mean)}",
            10000,
        )

        if self.power_scan_active:
            self._handle_power_scan_spectrum_ready(record)

    @Slot(object)
    def _on_spectrometer_info(self, info: SpectrometerInfo) -> None:
        self.spectrometer_info = info

        if math.isfinite(info.max_intensity):
            msg = (
                f"Spectrometer: {info.name}, serial {info.serial_number or '--'}, "
                f"max intensity {info.max_intensity:.0f} counts"
            )
        else:
            msg = f"Spectrometer: {info.name}, serial {info.serial_number or '--'}"

        self.statusBar().showMessage(msg, 10000)

    @Slot(object)
    def _on_spectrometer_capabilities_ready(self, caps: SpectrometerCapabilities) -> None:
        self.spectrometer_capabilities = caps

    @Slot(object)
    def _on_monitor_cleared(self) -> None:
        self.statusBar().showMessage("Spectrum monitor cleared.", 5000)

    @Slot(str)
    def _show_status_message(self, message: str) -> None:
        self.statusBar().showMessage(str(message), 10000)

    @Slot(str)
    def _on_laser_error(self, message: str) -> None:
        print(message)
        self.statusBar().showMessage("Laser controller error. See console output.", 10000)

    @Slot(object)
    def _on_lasers_ready(self, lasers: object) -> None:
        self.laser_panel.set_lasers(lasers)

    @Slot(str, int, bool)
    def _on_laser_enable_requested(self, port: str, channel: int, enabled: bool) -> None:
        laser = self.laser_panel.laser_by_key(port, channel)

        if enabled and laser is not None:
            self._maybe_update_power_meter_wavelength_for_laser(laser)

        self.laser_set_enabled_requested.emit(port, channel, enabled)

    @Slot(str, int, float)
    def _on_scan_laser_power_set(self, port: str, channel: int, power_w: float) -> None:
        if self.calibration_active:
            laser = self.calibration_laser

            if laser is None:
                return

            if str(port) != str(laser.port) or int(channel) != int(laser.channel):
                return

            settle_ms = int(round(1000.0 * self.scan_panel.settling_seconds()))

            QTimer.singleShot(settle_ms, self._read_current_calibration_power)
            return

        if not self.power_scan_active:
            return

        laser = self.power_scan_laser

        if laser is None:
            return

        if str(port) != str(laser.port) or int(channel) != int(laser.channel):
            return

        settle_ms = int(round(1000.0 * self.scan_panel.settling_seconds()))

        self.statusBar().showMessage(
            f"Laser set to {float(power_w):.6e} W. Settling for {settle_ms / 1000.0:.2f} s.",
            10000,
        )

        QTimer.singleShot(settle_ms, self._acquire_current_power_scan_point)

    @Slot(str, int, bool)
    def _on_scan_laser_enabled_set(self, port: str, channel: int, enabled: bool) -> None:
        if self.calibration_active:
            laser = self.calibration_laser

            if laser is not None and str(port) == str(laser.port) and int(channel) == int(laser.channel):
                if enabled:
                    self._maybe_update_power_meter_wavelength_for_laser(laser)
                    self._start_next_calibration_point()
                else:
                    self._finish_calibration_scan("Calibration complete. Laser disabled.")
            return

        if not self.power_scan_active:
            return

        laser = self.power_scan_laser

        if laser is None:
            return

        if str(port) != str(laser.port) or int(channel) != int(laser.channel):
            return

        if enabled:
            self._maybe_update_power_meter_wavelength_for_laser(laser)
            self._start_next_power_scan_point()
        else:
            self._finish_power_scan("Power scan complete. Laser disabled.")

    @Slot(str)
    def _on_power_monitor_mode_changed(self, mode: str) -> None:
        mode = str(mode)

        if mode not in {"live", "spectra_only"}:
            mode = "live"

        self.power_monitor_settings.mode = mode
        self.power_monitor_settings.polling_enabled = mode == "live"

        self._apply_power_monitor_settings()

        if mode == "live":
            self.statusBar().showMessage("Power monitor mode: live readings.", 5000)
        else:
            self.statusBar().showMessage("Power monitor mode: spectra only.", 5000)

    @Slot(bool)
    def _on_auto_power_meter_wavelength_changed(self, enabled: bool) -> None:
        self.auto_update_power_meter_wavelength = bool(enabled)

        state = "enabled" if enabled else "disabled"
        self.statusBar().showMessage(f"Auto Newport wavelength update {state}.", 5000)

    @Slot(int)
    def _on_power_meter_wavelength_ready(self, wavelength_nm: int) -> None:
        self.last_power_meter_wavelength_nm = int(wavelength_nm)
        self.power_panel.set_power_meter_wavelength_nm(int(wavelength_nm))
        self.statusBar().showMessage(
            f"Newport wavelength: {int(wavelength_nm)} nm",
            5000,
        )

    @Slot(str, object)
    def _on_power_read_once_complete(self, tag: str, snapshot: PowerSnapshot) -> None:
        if not str(tag).startswith("calibration:"):
            return

        if not self.calibration_active:
            return

        if not snapshot.powers_w:
            return

        measured_w = self._calibration_power_from_snapshot_or_emulator(snapshot)
        self.calibration_readings_w.append(measured_w)
        self.calibration_read_index += 1

        if self.calibration_read_index < self.scan_panel.calibration_reads_per_point():
            QTimer.singleShot(0, self._read_current_calibration_power)
            return

        arr = np.asarray(self.calibration_readings_w, dtype=float)
        arr = arr[np.isfinite(arr)]

        point = self.calibration_points[self.calibration_index]
        laser = self.calibration_laser

        if arr.size == 0:
            mean_w = float("nan")
            std_w = float("nan")
        else:
            mean_w = float(np.mean(arr))
            std_w = float(np.std(arr, ddof=0))

        self.calibration_results.append(
            {
                "timestamp_utc": utc_now_iso(),
                "port": str(laser.port),
                "box_id": str(laser.box_id),
                "channel": int(laser.channel),
                "wavelength_nm": float(laser.wavelength_nm),
                "setpoint_w": float(point.setpoint_w),
                "measured_power_mean_w": mean_w,
                "measured_power_std_w": std_w,
                "n_reads": int(arr.size),
                "filter_state": "none",
            }
        )

        self.calibration_index += 1
        QTimer.singleShot(0, self._start_next_calibration_point)

    @Slot(object)
    def _on_background_ready(self, background: BackgroundSpectrum) -> None:
        self.statusBar().showMessage(
            (
                f"Background captured: {background.timestamp_utc}, "
                f"{background.integration_ms} ms, avg={background.averages}"
            ),
            15000,
        )

    @Slot()
    def _on_background_cleared(self) -> None:
        self.statusBar().showMessage("Background spectrum cleared.", 10000)

    @Slot(float)
    def _on_spectrometer_temperature_ready(self, temperature_c: float) -> None:
        self.statusBar().showMessage(
            f"CCD temperature: {float(temperature_c):.2f} °C",
            10000,
        )

    def capture_background(self) -> None:
        settings = self._settings()
        settings.subtract_background = False

        self.statusBar().showMessage("Capturing background spectrum...", 5000)
        self.background_capture_requested.emit(settings)

    def _mean_spectrum_power_snapshot(self, record: SpectrumRecord) -> PowerSnapshot:
        n = min(len(record.p_before.powers_w), len(record.p_after.powers_w))

        powers_w = []
        for i in range(n):
            powers_w.append(
                0.5 * (
                    float(record.p_before.powers_w[i])
                    + float(record.p_after.powers_w[i])
                )
            )

        n_status = min(len(record.p_before.pm_status), len(record.p_after.pm_status))

        # Status words are treated as bitfields here.
        # If either before/after reports a flag, the combined value preserves it.
        pm_status = []
        for i in range(n_status):
            pm_status.append(
                int(record.p_before.pm_status[i]) | int(record.p_after.pm_status[i])
            )

        command_status = int(record.p_before.command_status) | int(record.p_after.command_status)

        return PowerSnapshot(
            powers_w=powers_w,
            pm_status=pm_status,
            command_status=command_status,
        )

    def _append_power_history(self, power: PowerSnapshot, *, source: str) -> None:
        elapsed_s = time.perf_counter() - self.app_t0

        point = PowerTracePoint(
            timestamp_utc=utc_now_iso(),
            elapsed_s=float(elapsed_s),
            source=str(source),
            powers_w=[float(x) for x in power.powers_w],
            pm_status=[int(x) for x in power.pm_status],
            command_status=int(power.command_status),
        )

        if self.full_power_logger is not None:
            self.full_power_logger.write_point(point)

        self.power_panel.append_point(point)

    def clear_monitor_track(self) -> None:
        self.monitor_panel.clear()
        self.statusBar().showMessage("Spectrum monitor cleared.", 5000)

    def clear_power_trace(self) -> None:
        self.power_panel.clear()
        self.statusBar().showMessage("Power trace cleared.", 5000)

    def clear_all_monitors(self) -> None:
        self.clear_power_trace()
        self.clear_monitor_track()
        self.statusBar().showMessage("Power and spectrum monitors cleared.", 5000)

    def toggle_live(self) -> None:
        enabled = not self.acquisition_panel.is_live_enabled()
        self.acquisition_panel.set_live_enabled(enabled)

        state = "ON" if enabled else "OFF"
        self.statusBar().showMessage(f"Live acquisition: {state}", 5000)

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

        self._apply_power_monitor_settings()
        self._apply_plot_style()
        self._update_autosave_indicator()
        
        self._save_preferences()

        self.statusBar().showMessage("Settings updated.", 5000)

    def show_power_details_dialog(self) -> None:
        dialog = PowerDetailsDialog(self.power_panel.points(), self)
        dialog.exec()

    def show_spectrometer_details_dialog(self) -> None:
        caps = getattr(self, "spectrometer_capabilities", None)

        if caps is None:
            QMessageBox.information(
                self,
                "No spectrometer details",
                "No spectrometer capabilities have been reported yet.",
            )
            return

        dialog = SpectrometerDetailsDialog(caps, self)

        dialog.tec_target_requested.connect(self.tec_target_requested.emit)
        dialog.tec_enabled_requested.connect(self.tec_enabled_requested.emit)
        dialog.temperature_refresh_requested.connect(
            self.spectrometer_temperature_requested.emit
        )

        dialog.exec()

    def start_full_power_log(self) -> None:
        if self.full_power_logger is not None:
            QMessageBox.information(self, "Power log active", "A full power log is already active.")
            return

        suggested_path = build_power_trace_path(
            self.file_name_settings,
            protect_existing=True,
        )

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Start full power log",
            str(suggested_path),
            "CSV files (*.csv);;All files (*)",
        )

        if not path:
            return

        self.full_power_logger = FullPowerLogger(Path(path))

        self.start_power_log_action.setEnabled(False)
        self.stop_power_log_action.setEnabled(True)

        self.statusBar().showMessage(f"Started full power log: {path}", 10000)

    def stop_full_power_log(self) -> None:
        if self.full_power_logger is None:
            return

        path = self.full_power_logger.path
        self.full_power_logger.close()
        self.full_power_logger = None

        self.start_power_log_action.setEnabled(True)
        self.stop_power_log_action.setEnabled(False)

        self.statusBar().showMessage(f"Stopped full power log: {path}", 10000)

    def _poll_power_tick(self) -> None:
        if not self.power_monitor_settings.polling_enabled:
            return

        if self.power_monitor_settings.mode != "live":
            return

        self.power_poll_requested.emit()

    def _apply_power_monitor_settings(self) -> None:
        self.power_panel.set_max_points(int(self.power_monitor_settings.max_points))
        self.power_panel.set_mode(self.power_monitor_settings.mode)

        self.power_timer.setInterval(int(self.power_monitor_settings.interval_ms))

        if (
            self.power_monitor_settings.polling_enabled
            and self.power_monitor_settings.mode == "live"
        ):
            self.power_timer.start()
        else:
            self.power_timer.stop()

        # Send a copy so the worker receives a stable snapshot of the settings.
        self.power_settings_changed.emit(replace(self.power_monitor_settings))

    def _maybe_update_power_meter_wavelength_for_laser(self, laser: LaserChannelInfo) -> None:
        if not self.auto_update_power_meter_wavelength:
            return

        if not math.isfinite(float(laser.wavelength_nm)):
            return

        wavelength_nm = int(round(float(laser.wavelength_nm)))

        if self.last_power_meter_wavelength_nm == wavelength_nm:
            return

        self.last_power_meter_wavelength_nm = wavelength_nm
        self.power_panel.set_power_meter_wavelength_nm(wavelength_nm)
        self.power_meter_wavelength_requested.emit(wavelength_nm)

    def _start_laser_controller(self) -> None:
        self.laser_thread = QThread(self)

        self.laser_controller = LaserController(
            emulate=bool(self.config.emulate_lasers),
            fallback_emulator=bool(self.config.laser_fallback_emulator),
            candidate_ports=self.config.obis_ports,
        )

        self.laser_controller.moveToThread(self.laser_thread)

        # Worker-thread startup.
        self.laser_thread.started.connect(
            self.laser_controller.refresh,
            Qt.ConnectionType.QueuedConnection,
        )

        # GUI -> worker.
        self.laser_panel.refresh_requested.connect(
            self.laser_controller.refresh,
            Qt.ConnectionType.QueuedConnection,
        )
        self.laser_panel.set_power_requested.connect(
            self.laser_controller.set_power_w,
            Qt.ConnectionType.QueuedConnection,
        )
        self.laser_panel.set_enabled_requested.connect(
            self._on_laser_enable_requested,
            Qt.ConnectionType.QueuedConnection,
        )
        self.laser_panel.disable_all_requested.connect(
            self.laser_controller.disable_all,
            Qt.ConnectionType.QueuedConnection,
        )
        self.laser_controller.power_set_complete.connect(
            self._on_scan_laser_power_set, 
            Qt.ConnectionType.QueuedConnection,
        )
        self.laser_controller.enabled_set_complete.connect(
            self._on_scan_laser_enabled_set, 
            Qt.ConnectionType.QueuedConnection,
        )
        # Worker -> GUI.
        self.laser_controller.lasers_ready.connect(
            self._on_lasers_ready,
            Qt.ConnectionType.QueuedConnection,
        )
        self.laser_controller.status.connect(
            self._show_status_message,
            Qt.ConnectionType.QueuedConnection,
        )
        self.laser_controller.error.connect(
            self._on_laser_error,
            Qt.ConnectionType.QueuedConnection,
        )
        
        self.laser_panel.set_cdrh_delay_requested.connect(
            self.laser_controller.set_cdrh_delay,
            Qt.ConnectionType.QueuedConnection
        )

        self.laser_thread.start()

    def _shutdown_laser_controller(self) -> None:
        if not hasattr(self, "laser_thread"):
            return

        if not hasattr(self, "laser_controller"):
            return

        if not self.laser_thread.isRunning():
            return

        try:
            self.laser_thread.requestInterruption()
        except Exception:
            pass

        try:
            QMetaObject.invokeMethod(
                self.laser_controller,
                "shutdown",
                Qt.ConnectionType.QueuedConnection,
            )
        except Exception:
            pass

        self.laser_thread.quit()

        if not self.laser_thread.wait(1500):
            print("Laser thread did not stop within 1.5 s; terminating.")
            self.laser_thread.terminate()
            self.laser_thread.wait(1500)

    def _on_laser_error(self, message: str) -> None:
        print(message)
        self.statusBar().showMessage("Laser controller error. See console output.", 10000)

    def _selected_scan_laser_or_warn(self):
        laser = self.laser_panel.selected_laser()

        if laser is None:
            QMessageBox.information(
                self,
                "No laser selected",
                "Select a laser in the Lasers tab before previewing or running a scan.",
            )
            return None

        return laser

    def start_calibration_scan(self) -> None:
        if self.power_scan_active or self.calibration_active:
            return

        laser = self._selected_scan_laser_or_warn()

        if laser is None:
            return

        try:
            points = self.scan_panel.make_points_for_laser(
                laser_min_setpoint_w=float(laser.min_setpoint_w),
                laser_max_setpoint_w=float(laser.max_setpoint_w),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Calibration preview failed", str(exc))
            return

        if not points:
            return

        self.scan_panel.set_points(points)

        self.calibration_active = True
        self.calibration_points = points
        self.calibration_laser = laser
        self.calibration_index = 0
        self.calibration_read_index = 0
        self.calibration_readings_w = []
        self.calibration_results = []

        self.scan_panel.set_running(True)
        self.acquisition_panel.set_live_enabled(False)

        self.statusBar().showMessage("Starting laser calibration scan...", 10000)

        if self.scan_panel.should_enable_before_scan():
            self.laser_set_enabled_requested.emit(
                str(laser.port),
                int(laser.channel),
                True,
            )
        else:
            self._start_next_calibration_point()

    def _start_next_calibration_point(self) -> None:
        if not self.calibration_active:
            return

        if self.calibration_index >= len(self.calibration_points):
            self._complete_calibration_curve()

            if self.scan_panel.should_disable_after_scan() and self.calibration_laser is not None:
                self.laser_set_enabled_requested.emit(
                    str(self.calibration_laser.port),
                    int(self.calibration_laser.channel),
                    False,
                )
                return

            self._finish_calibration_scan("Calibration complete.")
            return

        point = self.calibration_points[self.calibration_index]
        laser = self.calibration_laser

        self.calibration_read_index = 0
        self.calibration_readings_w = []

        self.statusBar().showMessage(
            (
                f"Calibration point {self.calibration_index + 1}/"
                f"{len(self.calibration_points)}: "
                f"setting laser to {point.setpoint_w:.6e} W"
            ),
            10000,
        )

        self.laser_set_power_requested.emit(
            str(laser.port),
            int(laser.channel),
            float(point.setpoint_w),
        )

    def _read_current_calibration_power(self) -> None:
        if not self.calibration_active:
            return

        tag = f"calibration:{self.calibration_index}:{self.calibration_read_index}"
        self.power_read_once_requested.emit(tag)

    def _calibration_power_from_snapshot_or_emulator(self, snapshot: PowerSnapshot) -> float:
        if (
            self.config.emulate
            and getattr(self.config, "emulate_lasers", False)
            and self.calibration_active
            and self.calibration_index < len(self.calibration_points)
        ):
            point = self.calibration_points[self.calibration_index]

            # Synthetic optical train transmission. Adjust if useful.
            transmission = 0.85

            # Deterministic tiny ripple so calibration is not perfectly trivial.
            ripple = 1.0 + 0.002 * np.sin(17.0 * float(point.setpoint_w))

            return float(point.setpoint_w) * transmission * ripple

        if not snapshot.powers_w:
            return float("nan")

        return float(snapshot.powers_w[0])

    def _complete_calibration_curve(self) -> None:
        setpoints = []
        measured = []

        for row in self.calibration_results:
            p_set = float(row["setpoint_w"])
            p_meas = float(row["measured_power_mean_w"])

            if np.isfinite(p_set) and np.isfinite(p_meas):
                setpoints.append(p_set)
                measured.append(p_meas)

        if len(setpoints) < 2:
            QMessageBox.warning(
                self,
                "Calibration failed",
                "Fewer than two valid calibration points were acquired.",
            )
            return

        try:
            self.current_laser_calibration = CalibrationCurve(
                setpoint_w=np.asarray(setpoints, dtype=float),
                measured_power_w=np.asarray(measured, dtype=float),
                filter_state="none",
            )

            self.statusBar().showMessage(
                f"Calibration ready with {len(setpoints)} point(s).",
                15000,
            )

        except Exception as exc:
            self.current_laser_calibration = None
            QMessageBox.warning(self, "Calibration invalid", str(exc))

    def _finish_calibration_scan(self, message: str) -> None:
        self.calibration_active = False
        self.calibration_points = []
        self.calibration_laser = None
        self.calibration_index = 0
        self.calibration_read_index = 0
        self.calibration_readings_w = []

        self.scan_panel.set_running(False)
        self.statusBar().showMessage(message, 15000)

    def _make_scan_points_for_laser(self, laser) -> list[PowerScanPoint]:
        calibration = None

        if self.scan_panel.scan_basis() == "expected_actual":
            calibration = self.current_laser_calibration

            # Without calibration, expected-actual scans assume setpoint -> actual identity.
            # That is acceptable if the user wants approximate powers and nominal filters.
            # Keep this permissive.
            # If you want to force calibration later, add a checkbox.

        if not self.filter_wheel_panel.planner_enabled():
            return self.scan_panel.make_points_for_laser(
                laser_min_setpoint_w=float(laser.min_setpoint_w),
                laser_max_setpoint_w=float(laser.max_setpoint_w),
                calibration=calibration,
            )

        # Filter planning treats requested powers as target actual powers.
        factor = self.scan_panel.power_factor()
        spacing = self.scan_panel.spacing()

        from planning.power_scan import make_requested_powers_w

        requested_powers_w = make_requested_powers_w(
            start_w=float(self.scan_panel.start_power.value()) * factor,
            stop_w=float(self.scan_panel.stop_power.value()) * factor,
            n_points=int(self.scan_panel.n_points.value()),
            spacing=spacing,
            custom_values_w=self.scan_panel.custom_powers_w() if spacing == "custom" else None,
        )

        states = enumerate_filter_states(self.filter_wheel_panel.filter_wheels())

        plan_steps = plan_min_filter_changes(
            target_powers_w=requested_powers_w,
            states=states,
            laser_min_setpoint_w=float(laser.min_setpoint_w),
            laser_max_setpoint_w=float(laser.max_setpoint_w),
            calibration=calibration,
        )

        points: list[PowerScanPoint] = []

        for step in plan_steps:
            points.append(
                PowerScanPoint(
                    index=int(step.index),
                    requested_power_w=float(step.target_power_w),
                    requested_basis="expected_actual",
                    setpoint_w=float(step.required_setpoint_w),
                    expected_actual_power_w=float(step.expected_actual_power_w),
                    filter_state=step.filter_state.label,
                )
            )

        return points

    def save_current_calibration(self) -> None:
        if self.current_laser_calibration is None:
            QMessageBox.information(self, "No calibration", "No calibration curve is available.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save calibration",
            "laser_calibration.csv",
            "CSV files (*.csv);;All files (*)",
        )

        if not path:
            return

        save_calibration_csv(
            Path(path),
            calibration=self.current_laser_calibration,
            rows=self.calibration_results,
        )

        self.statusBar().showMessage(f"Saved calibration: {path}", 10000)

    def load_calibration(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load calibration",
            "",
            "CSV files (*.csv);;All files (*)",
        )

        if not path:
            return

        try:
            calibration, rows = load_calibration_csv(Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Load calibration failed", str(exc))
            return

        self.current_laser_calibration = calibration
        self.calibration_results = rows

        self.statusBar().showMessage(
            f"Loaded calibration with {len(calibration.setpoint_w)} point(s).",
            10000,
        )

    def preview_power_scan(self) -> None:
        laser = self._selected_scan_laser_or_warn()

        if laser is None:
            return

        try:
            points = self._make_scan_points_for_laser(laser)
            self.scan_panel.set_points(points)
            
            self.statusBar().showMessage(
                f"Prepared {len(points)} scan point(s) for {laser.wavelength_nm} nm laser.",
                10000,
            )
        
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Power scan preview failed",
                str(exc),
            )

    def _ensure_filter_state_for_point(self, point: PowerScanPoint) -> bool:
        state = str(point.filter_state or "none")

        if state == "none":
            self.current_scan_filter_state = state
            return True

        if self.current_scan_filter_state == state:
            return True

        result = QMessageBox.information(
            self,
            "Set neutral-density filters",
            (
                "Set the manual filter wheels to:\n\n"
                f"{state}\n\n"
                "Click OK to continue the scan."
            ),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )

        if result != QMessageBox.StandardButton.Ok:
            return False

        self.current_scan_filter_state = state
        return True

    def _start_next_power_scan_point(self) -> None:
        if self.power_scan_abort_requested:
            self._finish_power_scan("Power scan aborted.")
            return

        if self.power_scan_point_index >= len(self.power_scan_points):
            if self.scan_panel.should_disable_after_scan() and self.power_scan_laser is not None:
                self.laser_set_enabled_requested.emit(
                    str(self.power_scan_laser.port),
                    int(self.power_scan_laser.channel),
                    False,
                )
                return

            self._finish_power_scan("Power scan complete.")
            return

        point = self.power_scan_points[self.power_scan_point_index]

        if not self._ensure_filter_state_for_point(point):
            self._finish_power_scan("Power scan aborted during filter change.")
            return

        laser = self.power_scan_laser

        self.statusBar().showMessage(
            (
                f"Scan point {self.power_scan_point_index + 1}/"
                f"{len(self.power_scan_points)}, repeat "
                f"{self.power_scan_repeat_index + 1}/{self.scan_panel.repeats()}: "
                f"setting laser to {point.setpoint_w:.6e} W"
            ),
            10000,
        )

        self.laser_set_power_requested.emit(
            str(laser.port),
            int(laser.channel),
            float(point.setpoint_w),
        )

    def start_power_scan(self) -> None:
        if self.power_scan_active:
            return

        laser = self._selected_scan_laser_or_warn()

        if laser is None:
            return

        if not self.scan_panel.points():
            self.preview_power_scan()

        points = self.scan_panel.points()

        if not points:
            QMessageBox.information(
                self,
                "No scan points",
                "Preview or define at least one scan point first.",
            )
            return

        self.power_scan_active = True
        self.power_scan_abort_requested = False
        self.power_scan_points = points
        self.power_scan_laser = laser
        self.power_scan_point_index = 0
        self.power_scan_repeat_index = 0
        self.current_scan_filter_state = None

        self.scan_panel.set_running(True)
        self.acquisition_panel.set_live_enabled(False)

        self.statusBar().showMessage("Starting power scan...", 10000)

        if self.scan_panel.should_enable_before_scan():
            self.laser_set_enabled_requested.emit(
                str(laser.port),
                int(laser.channel),
                True,
            )
        else:
            self._start_next_power_scan_point()

    def _finish_power_scan(self, message: str) -> None:
        self.power_scan_active = False
        self.power_scan_abort_requested = False
        self.power_scan_points = []
        self.power_scan_laser = None
        self.power_scan_point_index = 0
        self.power_scan_repeat_index = 0
        self.current_scan_filter_state = None

        self.scan_panel.set_running(False)

        self.statusBar().showMessage(message, 15000)

    def abort_power_scan(self) -> None:
        if not self.power_scan_active:
            return

        self.power_scan_abort_requested = True
        self.statusBar().showMessage("Power scan abort requested.", 10000)

    def _acquire_current_power_scan_point(self) -> None:
        if not self.power_scan_active:
            return

        if self.power_scan_abort_requested:
            self._finish_power_scan("Power scan aborted.")
            return

        self.take_spectrum()

    def _handle_power_scan_spectrum_ready(self, record: SpectrumRecord) -> None:
        if self.scan_panel.should_autosave_scan_spectra():
            self._autosave_spectrum(record)

        self.power_scan_repeat_index += 1

        if self.power_scan_repeat_index >= self.scan_panel.repeats():
            self.power_scan_repeat_index = 0
            self.power_scan_point_index += 1

        QTimer.singleShot(0, self._start_next_power_scan_point)

    def _signal_warning_threshold_counts(self) -> float:
        settings = self.signal_warning_settings

        if settings.use_spectrometer_max:
            max_intensity = float(self.spectrometer_info.max_intensity)

            if math.isfinite(max_intensity) and max_intensity > 0:
                return settings.fraction_of_spectrometer_max * max_intensity

        return float(settings.absolute_threshold_counts)

    def _check_signal_warning(self, record: SpectrumRecord) -> None:
        settings = self.signal_warning_settings

        if not settings.enabled:
            return

        signal_max = float(record.signal_max_counts)

        if not math.isfinite(signal_max):
            signal_max = float(np.nanmax(record.intensities_counts))

        threshold = self._signal_warning_threshold_counts()

        if not math.isfinite(threshold) or threshold <= 0:
            return

        if signal_max < threshold:
            return

        limit = float(self.spectrometer_info.max_intensity)

        if math.isfinite(limit) and limit > 0:
            percent = 100.0 * signal_max / limit
            message = (
                f"High spectrometer signal detected.\n\n"
                f"Maximum signal: {signal_max:.0f} counts\n"
                f"Spectrometer max: {limit:.0f} counts\n"
                f"Fraction of max: {percent:.2f}%\n"
                f"Warning threshold: {threshold:.0f} counts"
            )
        else:
            message = (
                f"High spectrometer signal detected.\n\n"
                f"Maximum signal: {signal_max:.0f} counts\n"
                f"Warning threshold: {threshold:.0f} counts"
            )

        self.statusBar().showMessage(message.replace("\n", " "), 15000)

        if not settings.popup_enabled:
            return

        now = time.perf_counter()

        if now - self.last_signal_warning_s < float(settings.popup_cooldown_s):
            return

        self.last_signal_warning_s = now

        QMessageBox.warning(
            self,
            "High spectrometer signal",
            message,
        )

    def _on_monitor_memory_warning(self, estimated_mb: float, n_points: int) -> None:
        QMessageBox.warning(
            self,
            "Monitor memory warning",
            (
                f"The scalar spectrum monitor is estimated to be using "
                f"about {estimated_mb:.1f} MB.\n\n"
                f"Current points: {n_points}\n\n"
                f"Clear the monitor if this run does not need the full scalar history."
            ),
        )

    def save_spectrum(self) -> None:
        if self.current_record is None:
            QMessageBox.information(self, "No spectrum", "No spectrum is currently loaded.")
            return

        suggested_path = build_spectrum_path(
            self.file_name_settings,
            self.current_record,
            protect_existing=True,
        )

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save spectrum",
            str(suggested_path),
            "CSV files (*.csv);;All files (*)",
        )

        if not path:
            return

        save_spectrum_record(Path(path), self.current_record)
        self.statusBar().showMessage(f"Saved {path}", 10000)

    def _autosave_spectrum(self, record: SpectrumRecord) -> None:
        path = build_spectrum_path(
            self.file_name_settings,
            record,
            protect_existing=True,
        )

        save_spectrum_record(path, record)
        self.statusBar().showMessage(f"Autosaved {path}", 10000)

    def _update_autosave_indicator(self) -> None:
        if self.file_name_settings.autosave_spectra:
            self.autosave_label.setText("Autosave: ON")
        else:
            self.autosave_label.setText("Autosave: OFF")

    def open_spectrum(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open spectrum",
            "",
            "CSV files (*.csv);;All files (*)",
        )

        if not path:
            return

        wavelengths_nm, intensities = load_spectrum_csv(Path(path))

        empty_power = PowerSnapshot([], [], 0)

        self.current_record = SpectrumRecord(
            timestamp_utc=utc_now_iso(),
            timestamp_s=time.perf_counter(),
            wavelengths_nm=wavelengths_nm,
            intensities_counts=intensities,
            p_before=empty_power,
            p_after=empty_power,
            integration_ms=0,
            averages=0,
            boxcar_width=0,
            correct_dark=False,
            correct_nonlinearity=False,
            field_value=0.0,
        )

        self.spectrum_curve.setData(wavelengths_nm, intensities)
        self.tabs.setCurrentWidget(self.spectrum_plot)
        self.statusBar().showMessage(f"Loaded {path}", 10000)

    def save_monitor_track(self) -> None:
        if not self.monitor_panel.has_points():
            QMessageBox.information(self, "No monitor data", "No monitor data are available.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save monitor track",
            "monitor_track.csv",
            "CSV files (*.csv);;All files (*)",
        )

        if not path:
            return

        self.monitor_panel.save_csv(Path(path))
        self.statusBar().showMessage(f"Saved {path}", 10000)

    def save_power_trace(self) -> None:
        points = list(self.power_panel.points())
        
        if not points:
            QMessageBox.information(self, "No power trace", "No power trace data are available.")
            return

        suggested_path = build_power_trace_path(
            self.file_name_settings,
            protect_existing=True,
        )

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save power trace",
            str(suggested_path),
            "CSV files (*.csv);;All files (*)",
        )

        if not path:
            return

        max_channels = 0
        for point in points:
            max_channels = max(max_channels, len(point.powers_w))

        header = ["timestamp_utc", "elapsed_s"]

        for i in range(max_channels):
            header.append(f"ch{i + 1}_power_W")

        with Path(path).open("w", newline="") as f:
            writer = csv.writer(f)

            writer.writerow(["# file_type", "power_trace"])
            writer.writerow(["# saved_utc", utc_now_iso()])
            writer.writerow(["# max_points", self.power_monitor_settings.max_points])
            writer.writerow(["# polling_interval_ms", self.power_monitor_settings.interval_ms])
            writer.writerow(header)

            for point in points:
                row = [
                    point.timestamp_utc,
                    f"{point.elapsed_s:.9f}",
                ]

                for i in range(max_channels):
                    if i < len(point.powers_w):
                        row.append(f"{point.powers_w[i]:.12e}")
                    else:
                        row.append("")

                writer.writerow(row)

        self.statusBar().showMessage(f"Saved {path}", 10000)

    def _save_preferences(self) -> None:
        settings = QSettings()

        # File naming.
        settings.setValue("files/save_directory", str(self.file_name_settings.save_directory))
        settings.setValue("files/base_name", self.file_name_settings.base_name)
        settings.setValue("files/run_identifier", self.file_name_settings.run_identifier)
        settings.setValue("files/notes", self.file_name_settings.notes)

        settings.setValue("files/include_date", self.file_name_settings.include_date)
        settings.setValue("files/include_time", self.file_name_settings.include_time)
        settings.setValue("files/include_power", self.file_name_settings.include_power)
        settings.setValue("files/include_field", self.file_name_settings.include_field)
        settings.setValue(
            "files/include_run_identifier",
            self.file_name_settings.include_run_identifier,
        )
        settings.setValue(
            "files/include_enumeration",
            self.file_name_settings.include_enumeration,
        )
        settings.setValue("files/autosave_spectra", self.file_name_settings.autosave_spectra)

        # Power monitor.
        settings.setValue("power/mode", self.power_monitor_settings.mode)
        settings.setValue(
            "power/append_spectrum_power",
            self.power_monitor_settings.append_spectrum_power,
        )
        settings.setValue("power/max_points", self.power_monitor_settings.max_points)
        settings.setValue("power/interval_ms", self.power_monitor_settings.interval_ms)
        settings.setValue(
            "power/validation_enabled",
            self.power_monitor_settings.validation_enabled,
        )
        settings.setValue(
            "power/max_valid_power_w",
            self.power_monitor_settings.max_valid_power_w,
        )
        settings.setValue(
            "power/invalid_power_retries",
            self.power_monitor_settings.invalid_power_retries,
        )
        settings.setValue(
            "power/invalid_power_retry_delay_s",
            self.power_monitor_settings.invalid_power_retry_delay_s,
        )

        # Signal warning.
        settings.setValue("warnings/enabled", self.signal_warning_settings.enabled)
        settings.setValue(
            "warnings/use_spectrometer_max",
            self.signal_warning_settings.use_spectrometer_max,
        )
        settings.setValue(
            "warnings/fraction_of_spectrometer_max",
            self.signal_warning_settings.fraction_of_spectrometer_max,
        )
        settings.setValue(
            "warnings/absolute_threshold_counts",
            self.signal_warning_settings.absolute_threshold_counts,
        )
        settings.setValue(
            "warnings/popup_enabled",
            self.signal_warning_settings.popup_enabled,
        )
        settings.setValue(
            "warnings/popup_cooldown_s",
            self.signal_warning_settings.popup_cooldown_s,
        )

        # Plot style.
        settings.setValue("plot/spectrum_color", self.plot_style_settings.spectrum_color)
        settings.setValue("plot/monitor_color", self.plot_style_settings.monitor_color)
        settings.setValue("plot/power_color", self.plot_style_settings.power_color)

        settings.setValue(
            "plot/spectrum_line_width",
            self.plot_style_settings.spectrum_line_width,
        )
        settings.setValue(
            "plot/monitor_line_width",
            self.plot_style_settings.monitor_line_width,
        )
        settings.setValue(
            "plot/power_line_width",
            self.plot_style_settings.power_line_width,
        )

        settings.setValue(
            "plot/spectrum_show_line",
            self.plot_style_settings.spectrum_show_line,
        )
        settings.setValue(
            "plot/monitor_show_line",
            self.plot_style_settings.monitor_show_line,
        )
        settings.setValue(
            "plot/power_show_line",
            self.plot_style_settings.power_show_line,
        )

        settings.setValue(
            "plot/spectrum_show_symbols",
            self.plot_style_settings.spectrum_show_symbols,
        )
        settings.setValue(
            "plot/monitor_show_symbols",
            self.plot_style_settings.monitor_show_symbols,
        )
        settings.setValue(
            "plot/power_show_symbols",
            self.plot_style_settings.power_show_symbols,
        )

        settings.setValue("plot/symbol", self.plot_style_settings.symbol)
        settings.setValue("plot/symbol_size", self.plot_style_settings.symbol_size)
        settings.setValue("plot/font_size", self.plot_style_settings.font_size)

        settings.setValue(
            "plot/spectrum_auto_range",
            self.plot_style_settings.spectrum_auto_range,
        )
        settings.setValue("plot/spectrum_x_min", self.plot_style_settings.spectrum_x_min)
        settings.setValue("plot/spectrum_x_max", self.plot_style_settings.spectrum_x_max)
        settings.setValue("plot/spectrum_y_min", self.plot_style_settings.spectrum_y_min)
        settings.setValue("plot/spectrum_y_max", self.plot_style_settings.spectrum_y_max)

        settings.setValue("filters/config_json", self.filter_wheel_panel.serialize())

        # Panel-level widgets.
        self.acquisition_panel.save_preferences(settings)
        self.monitor_panel.save_preferences(settings)
        self.power_panel.save_preferences(settings)
        self.laser_panel.save_preferences(settings)
        self.scan_panel.save_preferences(settings)

        settings.sync()

    def closeEvent(self, event) -> None:
        self._save_preferences()
        self._save_window_layout()

        self.acquisition_panel.set_live_enabled(False)
        self.power_timer.stop()
        self.live_timer.stop()

        self.stop_full_power_log()

        # Existing QEPro/Newport worker shutdown.
        try:
            QMetaObject.invokeMethod(
                self.controller,
                "shutdown",
                Qt.ConnectionType.QueuedConnection,
            )
        except Exception:
            self.shutdown_requested.emit()

        self.controller_thread.quit()
        self.controller_thread.wait(3000)

        self._shutdown_laser_controller()

        super().closeEvent(event)
