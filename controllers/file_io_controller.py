from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from core.records import PowerTracePoint, SpectrumRecord
from core.settings import FileNameSettings, PowerMonitorSettings
from io_utils.file_naming import (
    build_gated_series_path,
    build_power_trace_path,
    build_spectrum_path,
)
from io_utils.gated_series_io import save_gated_series_csv
from io_utils.power_logging import FullPowerLogger
from io_utils.power_trace_io import save_power_trace_csv
from io_utils.spectrum_io import load_spectrum_record, save_spectrum_record

_T = TypeVar("_T")
_FAILED = object()


class FileIOController(QObject):
    """GUI-thread file operations and full-run power logging."""

    record_loaded = Signal(object)
    status_requested = Signal(str, int)
    power_log_state_changed = Signal(bool)

    def __init__(
        self,
        *,
        parent: QWidget,
        file_settings: FileNameSettings,
        power_settings: PowerMonitorSettings,
        monitor_panel,
        power_panel,
    ) -> None:
        super().__init__(parent)
        self.parent_widget = parent
        self.file_settings = file_settings
        self.power_settings = power_settings
        self.monitor_panel = monitor_panel
        self.power_panel = power_panel
        self.current_record: SpectrumRecord | None = None
        self.full_power_logger: FullPowerLogger | None = None

    def update_settings(
        self,
        file_settings: FileNameSettings,
        power_settings: PowerMonitorSettings,
    ) -> None:
        self.file_settings = file_settings
        self.power_settings = power_settings

    def set_current_record(self, record: SpectrumRecord | None) -> None:
        self.current_record = record

    def write_power_point(self, point: PowerTracePoint) -> None:
        if self.full_power_logger is None:
            return
        try:
            self.full_power_logger.write_point(point)
        except OSError as exc:
            self._show_error("Power logging failed", exc)
            self.stop_full_power_log()

    def _show_error(self, title: str, error: BaseException) -> None:
        QMessageBox.critical(self.parent_widget, title, str(error))
        self.status_requested.emit(f"{title}: {error}", 15_000)

    def _run_file_operation(
        self,
        title: str,
        operation: Callable[[], _T],
    ) -> _T | object:
        try:
            return operation()
        except (OSError, ValueError, RuntimeError) as exc:
            self._show_error(title, exc)
            return _FAILED

    @Slot()
    def save_spectrum(self) -> None:
        if self.current_record is None:
            QMessageBox.information(
                self.parent_widget,
                "No spectrum",
                "No spectrum is currently loaded.",
            )
            return

        suggested = build_spectrum_path(
            self.file_settings,
            self.current_record,
            protect_existing=True,
        )
        path, _ = QFileDialog.getSaveFileName(
            self.parent_widget,
            "Save spectrum",
            str(suggested),
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return

        result = self._run_file_operation(
            "Save spectrum failed",
            lambda: save_spectrum_record(Path(path), self.current_record),
        )
        if result is not _FAILED:
            self.status_requested.emit(f"Saved {path}", 10_000)

    @Slot(object)
    def autosave_spectrum(self, record: SpectrumRecord) -> None:
        def operation() -> Path:
            path = build_spectrum_path(
                self.file_settings,
                record,
                protect_existing=True,
            )
            save_spectrum_record(path, record)
            return path

        path = self._run_file_operation("Autosave failed", operation)
        if path is not _FAILED:
            self.status_requested.emit(f"Autosaved {path}", 10_000)

    @Slot(object)
    def autosave_gated_series(self, series) -> None:
        def operation() -> Path:
            path = build_gated_series_path(
                self.file_settings,
                series,
                protect_existing=True,
            )
            save_gated_series_csv(path, series)
            return path

        path = self._run_file_operation("Gated-series autosave failed", operation)
        if path is not _FAILED:
            self.status_requested.emit(f"Autosaved averaged gated series {path}", 15_000)

    @Slot()
    def open_spectrum(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self.parent_widget,
            "Open spectrum",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return

        loaded = self._run_file_operation(
            "Open spectrum failed",
            lambda: load_spectrum_record(Path(path)),
        )
        if loaded is _FAILED:
            return

        record = loaded
        self.current_record = record
        self.record_loaded.emit(record)
        self.status_requested.emit(f"Loaded {path}", 10_000)

    @Slot()
    def save_monitor_track(self) -> None:
        if not self.monitor_panel.has_points():
            QMessageBox.information(
                self.parent_widget,
                "No monitor data",
                "No monitor data are available.",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self.parent_widget,
            "Save monitor track",
            "monitor_track.csv",
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return

        result = self._run_file_operation(
            "Save monitor track failed",
            lambda: self.monitor_panel.save_csv(Path(path)),
        )
        if result is not _FAILED:
            self.status_requested.emit(f"Saved {path}", 10_000)

    @Slot()
    def save_power_trace(self) -> None:
        points = self.power_panel.points()
        if not points:
            QMessageBox.information(
                self.parent_widget,
                "No power trace",
                "No power trace data are available.",
            )
            return

        suggested = build_power_trace_path(
            self.file_settings,
            protect_existing=True,
        )
        path, _ = QFileDialog.getSaveFileName(
            self.parent_widget,
            "Save power trace",
            str(suggested),
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return

        result = self._run_file_operation(
            "Save power trace failed",
            lambda: save_power_trace_csv(Path(path), points, self.power_settings),
        )
        if result is not _FAILED:
            self.status_requested.emit(f"Saved {path}", 10_000)

    @Slot()
    def start_full_power_log(self) -> None:
        if self.full_power_logger is not None:
            QMessageBox.information(
                self.parent_widget,
                "Power log active",
                "A full power log is already active.",
            )
            return

        suggested = build_power_trace_path(
            self.file_settings,
            protect_existing=True,
        )
        path, _ = QFileDialog.getSaveFileName(
            self.parent_widget,
            "Start full power log",
            str(suggested),
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return

        try:
            self.full_power_logger = FullPowerLogger(Path(path))
        except OSError as exc:
            self._show_error("Start power log failed", exc)
            return

        self.power_log_state_changed.emit(True)
        self.status_requested.emit(f"Started full power log: {path}", 10_000)

    @Slot()
    def stop_full_power_log(self) -> None:
        if self.full_power_logger is None:
            return

        logger = self.full_power_logger
        self.full_power_logger = None
        try:
            logger.close()
        except OSError as exc:
            self._show_error("Stop power log failed", exc)
        finally:
            self.power_log_state_changed.emit(False)

        self.status_requested.emit(f"Stopped full power log: {logger.path}", 10_000)

    @Slot()
    def copy_current_spectrum_data(self) -> None:
        if self.current_record is None:
            QMessageBox.information(
                self.parent_widget,
                "No spectrum",
                "No spectrum is currently loaded.",
            )
            return

        lines = ["wavelength_nm\tintensity_counts"]
        lines.extend(
            f"{wavelength:.12e}\t{intensity:.12e}"
            for wavelength, intensity in zip(
                self.current_record.wavelengths_nm,
                self.current_record.intensities_counts,
                strict=True,
            )
        )
        QApplication.clipboard().setText("\n".join(lines))
        self.status_requested.emit("Spectrum data copied to clipboard.", 5000)

    def close(self) -> None:
        self.stop_full_power_log()
