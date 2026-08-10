from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMainWindow

from core.preferences import load_dataclass, save_dataclass
from core.settings import (
    FileNameSettings,
    PlotStyleSettings,
    PowerMonitorSettings,
    SignalWarningSettings,
)


class PreferencesController:
    """Persists application dataclasses, panel state, and window layout."""

    def __init__(
        self,
        *,
        window: QMainWindow,
        file_settings: FileNameSettings,
        power_settings: PowerMonitorSettings,
        warning_settings: SignalWarningSettings,
        plot_settings: PlotStyleSettings,
        acquisition_panel,
        monitor_panel,
        power_panel,
        laser_panel,
        scan_panel,
        filter_wheel_panel,
    ) -> None:
        self.window = window
        self.acquisition_panel = acquisition_panel
        self.monitor_panel = monitor_panel
        self.power_panel = power_panel
        self.laser_panel = laser_panel
        self.scan_panel = scan_panel
        self.filter_wheel_panel = filter_wheel_panel
        self.update_dataclasses(
            file_settings,
            power_settings,
            warning_settings,
            plot_settings,
        )

    def update_dataclasses(
        self,
        file_settings: FileNameSettings,
        power_settings: PowerMonitorSettings,
        warning_settings: SignalWarningSettings,
        plot_settings: PlotStyleSettings,
    ) -> None:
        self.file_settings = file_settings
        self.power_settings = power_settings
        self.warning_settings = warning_settings
        self.plot_settings = plot_settings

    def load(self) -> tuple[bool, bool]:
        """Load preferences and return ``(auto_wavelength, scan_timing)``."""

        settings = QSettings()
        load_dataclass(settings, "files", self.file_settings)
        load_dataclass(settings, "power", self.power_settings)
        load_dataclass(settings, "warnings", self.warning_settings)
        load_dataclass(settings, "plot", self.plot_settings)

        if self.power_settings.mode not in {"live", "spectra_only"}:
            self.power_settings.mode = "live"

        filter_json = settings.value("filters/config_json", "", type=str)
        if filter_json:
            try:
                self.filter_wheel_panel.deserialize(filter_json)
            except (TypeError, ValueError):
                # Ignore old or corrupt local filter configuration.
                pass

        self.acquisition_panel.load_preferences(settings)
        self.monitor_panel.load_preferences(settings)
        self.power_panel.load_preferences(settings)
        self.laser_panel.load_preferences(settings)
        self.scan_panel.load_preferences(settings)

        auto_wavelength = self.power_panel.auto_wavelength_enabled()
        scan_timing = settings.value("tools/scan_timing", False, type=bool)
        return bool(auto_wavelength), bool(scan_timing)

    def save(self, *, scan_timing: bool) -> None:
        settings = QSettings()
        save_dataclass(settings, "files", self.file_settings)
        save_dataclass(settings, "power", self.power_settings)
        save_dataclass(settings, "warnings", self.warning_settings)
        save_dataclass(settings, "plot", self.plot_settings)
        settings.setValue("filters/config_json", self.filter_wheel_panel.serialize())
        settings.setValue("tools/scan_timing", bool(scan_timing))

        self.acquisition_panel.save_preferences(settings)
        self.monitor_panel.save_preferences(settings)
        self.power_panel.save_preferences(settings)
        self.laser_panel.save_preferences(settings)
        self.scan_panel.save_preferences(settings)
        settings.sync()

    def restore_window_layout(self) -> bool:
        settings = QSettings()
        restored = False
        geometry = settings.value("window/geometry")
        if geometry is not None:
            restored = bool(self.window.restoreGeometry(geometry)) or restored
        state = settings.value("window/state")
        if state is not None:
            restored = bool(self.window.restoreState(state)) or restored
        return restored

    def save_window_layout(self) -> None:
        settings = QSettings()
        settings.setValue("window/geometry", self.window.saveGeometry())
        settings.setValue("window/state", self.window.saveState())
        settings.sync()

    @staticmethod
    def clear_window_layout() -> None:
        settings = QSettings()
        settings.remove("window/geometry")
        settings.remove("window/state")
        settings.sync()
