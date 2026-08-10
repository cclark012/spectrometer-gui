from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMainWindow, QSizePolicy, QToolBar, QWidget


@dataclass(slots=True)
class MainWindowActions:
    open_spectrum: QAction
    save_spectrum: QAction
    save_monitor: QAction
    save_power_trace: QAction
    start_power_log: QAction
    stop_power_log: QAction
    acquire: QAction
    spectrum_auto_range: QAction
    scan_timing: QAction
    toolbar: QToolBar
    power_label: QLabel


def _action(
    parent: QMainWindow,
    text: str,
    callback: Callable,
    *,
    shortcut: str | None = None,
    checkable: bool = False,
) -> QAction:
    action = QAction(text, parent)
    action.setCheckable(bool(checkable))
    if shortcut:
        action.setShortcut(shortcut)
    action.triggered.connect(callback)
    return action


def build_main_window_actions(window: QMainWindow) -> MainWindowActions:
    """Build the menus and acquisition toolbar for ``MainWindow``.

    The function intentionally depends only on the public callbacks exposed by the
    window. Keeping menu construction here prevents the top-level window from
    becoming a long list of QAction boilerplate.
    """

    file_menu = window.menuBar().addMenu("&File")
    open_action = _action(
        window,
        "Open Spectrum...",
        window.file_io.open_spectrum,
        shortcut="Ctrl+O",
    )
    save_action = _action(
        window,
        "Save Spectrum...",
        window.file_io.save_spectrum,
        shortcut="Ctrl+S",
    )
    save_monitor_action = _action(
        window,
        "Save Monitor Track...",
        window.file_io.save_monitor_track,
    )
    save_power_action = _action(
        window,
        "Save Power Trace...",
        window.file_io.save_power_trace,
    )
    start_power_log_action = _action(
        window,
        "Start Full Power Log...",
        window.file_io.start_full_power_log,
    )
    stop_power_log_action = _action(
        window,
        "Stop Full Power Log",
        window.file_io.stop_full_power_log,
    )
    stop_power_log_action.setEnabled(False)

    for action in (
        open_action,
        save_action,
        save_monitor_action,
        save_power_action,
    ):
        file_menu.addAction(action)
    file_menu.addSeparator()
    file_menu.addAction(start_power_log_action)
    file_menu.addAction(stop_power_log_action)
    file_menu.addSeparator()
    file_menu.addAction(_action(window, "Quit", window.close, shortcut="Ctrl+Q"))

    edit_menu = window.menuBar().addMenu("&Edit")
    edit_menu.addAction(
        _action(
            window,
            "Copy Current Spectrum Data",
            window.file_io.copy_current_spectrum_data,
        )
    )
    edit_menu.addSeparator()
    edit_menu.addAction(_action(window, "Clear Spectrum", window.clear_spectrum))
    edit_menu.addAction(_action(window, "Clear Monitor", window.clear_monitor_track))
    edit_menu.addAction(_action(window, "Clear Power Trace", window.clear_power_trace))
    edit_menu.addAction(
        _action(
            window,
            "Clear All Monitors",
            window.clear_all_monitors,
            shortcut="Ctrl+Shift+C",
        )
    )

    view_menu = window.menuBar().addMenu("&View")
    spectrum_auto_range_action = _action(
        window,
        "Spectrum Auto Range",
        window._on_spectrum_auto_range_toggled,
        checkable=True,
    )
    spectrum_auto_range_action.setChecked(
        window.plot_style_settings.spectrum_auto_range
    )
    view_menu.addAction(spectrum_auto_range_action)
    view_menu.addAction(
        _action(
            window,
            "Set Spectrum Axis Limits...",
            window.show_spectrum_axis_dialog,
        )
    )
    view_menu.addAction(
        _action(
            window,
            "Use Current View as Manual Limits",
            window.use_current_spectrum_view_as_limits,
        )
    )
    view_menu.addAction(
        _action(window, "Auto Range Spectrum Now", window.spectrum_panel.auto_range_now)
    )
    view_menu.addSeparator()
    view_menu.addAction(window.controls_dock.toggleViewAction())
    view_menu.addAction(window.power_dock.toggleViewAction())
    view_menu.addAction(_action(window, "Reset Window Layout", window.reset_window_layout))

    tools_menu = window.menuBar().addMenu("&Tools")
    tools_menu.addAction(
        _action(window, "Application Settings...", window.open_settings_dialog)
    )
    tools_menu.addAction(
        _action(
            window,
            "Spectrometer Details...",
            window.show_spectrometer_details_dialog,
        )
    )
    tools_menu.addAction(
        _action(window, "Power Trace Details...", window.show_power_details_dialog)
    )
    tools_menu.addSeparator()
    tools_menu.addAction(
        _action(
            window,
            "Refresh Lasers",
            lambda _checked=False: window.laser_panel.refresh_requested.emit(),
        )
    )
    tools_menu.addAction(
        _action(
            window,
            "Disable All Lasers",
            lambda _checked=False: window.laser_panel.disable_all_requested.emit(),
        )
    )
    tools_menu.addSeparator()
    scan_timing_action = _action(
        window,
        "Enable Scan Timing Log",
        window._on_scan_timing_toggled,
        checkable=True,
    )
    tools_menu.addAction(scan_timing_action)
    tools_menu.addAction(
        _action(
            window,
            "Toggle Live Acquisition",
            window.toggle_live,
            shortcut="Ctrl+L",
        )
    )

    help_menu = window.menuBar().addMenu("&Help")
    help_menu.addAction(_action(window, "Keyboard Shortcuts", window.show_shortcuts))
    help_menu.addAction(_action(window, "Open GitHub Repository", window.open_github))
    help_menu.addAction(_action(window, "About", window.show_about))

    toolbar = QToolBar("Acquisition", window)
    toolbar.setObjectName("acquisition_toolbar")
    toolbar.setMovable(False)
    window.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    acquire_action = _action(
        window,
        "Take Spectrum",
        window.take_spectrum,
        shortcut="F5",
    )
    acquire_action.setStatusTip("Take spectrum (F5)")
    toolbar.addAction(acquire_action)
    toolbar.addAction(save_action)
    toolbar.addAction(open_action)

    spacer = QWidget()
    spacer.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.Preferred,
    )
    toolbar.addWidget(spacer)

    power_label = QLabel("Power: --")
    power_label.setMinimumWidth(220)
    power_label.setAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    toolbar.addWidget(power_label)

    return MainWindowActions(
        open_spectrum=open_action,
        save_spectrum=save_action,
        save_monitor=save_monitor_action,
        save_power_trace=save_power_action,
        start_power_log=start_power_log_action,
        stop_power_log=stop_power_log_action,
        acquire=acquire_action,
        spectrum_auto_range=spectrum_auto_range_action,
        scan_timing=scan_timing_action,
        toolbar=toolbar,
        power_label=power_label,
    )
