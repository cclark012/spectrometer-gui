from __future__ import annotations

import csv
import math
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import QSettings, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.preferences import get_bool, get_float, get_str
from core.records import MonitorTracePoint, SpectrumRecord
from core.time_utils import utc_now_iso
from panels.monitor_views import HAS_GL_3D, FieldPowerMapView, Monitor3DView
from processing.monitor_metrics import (
    MonitorCaptureConfig,
    build_monitor_point,
    monitor_x,
    monitor_y,
)

ESTIMATED_MONITOR_POINT_BYTES = 768


def label_with_units(label: str, units: str) -> str:
    return f"{label} ({units})" if units else label


class MonitorPanel(QWidget):
    """Stores scalar spectrum metrics and displays them in 2D/map/3D views.

    Acquired records are never discarded by redraw throttling. New points are added
    immediately, while plot updates are coalesced to the configured redraw interval.
    """

    save_requested = Signal()
    cleared = Signal()
    memory_warning_requested = Signal(float, int)
    redrawn = Signal()

    def __init__(
        self,
        *,
        memory_warning_mb: float = 50.0,
        redraw_interval_ms: int = 200,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self.monitor_trace: list[MonitorTracePoint] = []
        self.memory_warning_mb = float(memory_warning_mb)
        self.memory_warning_issued = False
        self._application_t0 = 0.0
        self._plot_dirty = False

        root = QVBoxLayout(self)
        self.monitor_stack = QStackedWidget()

        self.monitor_plot = pg.PlotWidget()
        self.monitor_plot.setLabel("bottom", "Time (s)")
        self.monitor_plot.setLabel("left", "Tracked quantity")
        self.monitor_curve = self.monitor_plot.plot()
        self._disable_si_prefixes(self.monitor_plot)
        self.monitor_stack.addWidget(self.monitor_plot)

        self.monitor_map_view = FieldPowerMapView(self)
        self.monitor_stack.addWidget(self.monitor_map_view)

        self.monitor_3d_view = Monitor3DView(self) if HAS_GL_3D and Monitor3DView else None
        if self.monitor_3d_view is not None:
            self.monitor_stack.addWidget(self.monitor_3d_view)

        root.addWidget(self.monitor_stack)
        root.addWidget(self._build_controls())

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(max(20, int(redraw_interval_ms)))
        self._redraw_timer.timeout.connect(self._redraw_if_dirty)
        self._redraw_timer.start()

        self.redraw()

    @staticmethod
    def _disable_si_prefixes(plot: pg.PlotWidget) -> None:
        for axis_name in ("bottom", "left"):
            axis = plot.getAxis(axis_name)
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)

    def _build_controls(self) -> QWidget:
        controls = QWidget()
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()

        self.plot_mode_combo = QComboBox()
        self.plot_mode_combo.addItem("2D monitor", "2d")
        self.plot_mode_combo.addItem("Field-power map", "map2d")
        if self.monitor_3d_view is not None:
            self.plot_mode_combo.addItem("3D: field / power / quantity", "3d")
        self.plot_mode_combo.currentIndexChanged.connect(lambda _index: self.redraw())

        self.track_enable = QCheckBox()
        self.track_enable.setChecked(True)

        self.track_quantity = QComboBox()
        self.track_quantity.addItems(
            [
                "Intensity at captured wavelength",
                "Integrated captured range",
                "Total integrated intensity",
                "Intensity at captured wavelength / power ch1",
                "Integrated captured range / power ch1",
                "Total integrated intensity / power ch1",
                "Peak intensity",
                "Peak wavelength",
                "Signal max",
                "Signal mean",
                "Power ch1",
            ]
        )
        self.track_quantity.currentTextChanged.connect(lambda _text: self.redraw())

        self.track_x_var = QComboBox()
        self.track_x_var.addItems(["Time", "Power ch1", "Magnetic field"])
        self.track_x_var.currentTextChanged.connect(lambda _text: self.redraw())

        self.track_wavelength = self._make_wavelength_spinbox(550.0)
        self.track_start_nm = self._make_wavelength_spinbox(450.0)
        self.track_stop_nm = self._make_wavelength_spinbox(750.0)

        form.addRow("Plot mode", self.plot_mode_combo)
        form.addRow("Track spectra", self.track_enable)
        form.addRow("Quantity", self.track_quantity)
        form.addRow("Versus", self.track_x_var)
        form.addRow("Wavelength", self.track_wavelength)
        form.addRow("Start", self.track_start_nm)
        form.addRow("Stop", self.track_stop_nm)
        layout.addLayout(form)

        buttons = QVBoxLayout()
        self.clear_button = QPushButton("Clear Monitor")
        self.clear_button.clicked.connect(self.clear)
        self.save_button = QPushButton("Save Monitor")
        self.save_button.clicked.connect(
            lambda _checked=False: self.save_requested.emit()
        )
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.save_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return controls

    @staticmethod
    def _make_wavelength_spinbox(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 2000.0)
        spin.setDecimals(1)
        spin.setSingleStep(10.0)
        spin.setValue(float(value))
        spin.setSuffix(" nm")
        return spin

    def tracking_enabled(self) -> bool:
        return bool(self.track_enable.isChecked())

    def points(self) -> list[MonitorTracePoint]:
        return list(self.monitor_trace)

    def has_points(self) -> bool:
        return bool(self.monitor_trace)

    def set_application_t0(self, app_t0: float) -> None:
        self._application_t0 = float(app_t0)

    def set_redraw_interval_ms(self, interval_ms: int) -> None:
        self._redraw_timer.setInterval(max(20, int(interval_ms)))

    def clear(self) -> None:
        self.monitor_trace.clear()
        self.memory_warning_issued = False
        self._plot_dirty = False
        self.monitor_curve.setData([], [])
        self.monitor_map_view.clear()
        if self.monitor_3d_view is not None:
            self.monitor_3d_view.clear()
        self.cleared.emit()

    def add_record(self, record: SpectrumRecord) -> None:
        config = MonitorCaptureConfig(
            target_wavelength_nm=float(self.track_wavelength.value()),
            integration_start_nm=float(self.track_start_nm.value()),
            integration_stop_nm=float(self.track_stop_nm.value()),
            application_t0_s=float(self._application_t0),
        )
        self.monitor_trace.append(build_monitor_point(record, config))
        self._check_memory_warning()
        self._plot_dirty = True

    def _redraw_if_dirty(self) -> None:
        if not self._plot_dirty:
            return
        self._plot_dirty = False
        self.redraw()
        self.redrawn.emit()

    def redraw(self) -> None:
        mode = str(self.plot_mode_combo.currentData())
        quantity_mode = self.track_quantity.currentText()

        if mode == "3d" and self.monitor_3d_view is not None:
            self.monitor_stack.setCurrentWidget(self.monitor_3d_view)
            self.monitor_3d_view.set_data(self.monitor_trace, quantity_mode)
            return

        if mode == "map2d":
            self.monitor_stack.setCurrentWidget(self.monitor_map_view)
            self.monitor_map_view.set_data(self.monitor_trace, quantity_mode)
            return

        self.monitor_stack.setCurrentWidget(self.monitor_plot)
        self._redraw_2d()

    def _redraw_2d(self) -> None:
        x_values: list[float] = []
        y_values: list[float] = []
        x_label, x_units = "X", ""
        y_label, y_units = "Y", ""
        x_mode = self.track_x_var.currentText()
        y_mode = self.track_quantity.currentText()

        for point in self.monitor_trace:
            x, x_label, x_units = monitor_x(point, x_mode)
            y, y_label, y_units = monitor_y(point, y_mode)
            if math.isfinite(x) and math.isfinite(y):
                x_values.append(float(x))
                y_values.append(float(y))

        self.monitor_curve.setData(x_values, y_values)
        self.monitor_plot.setLabel("bottom", label_with_units(x_label, x_units))
        self.monitor_plot.setLabel("left", label_with_units(y_label, y_units))

    def estimated_memory_mb(self) -> float:
        return (
            len(self.monitor_trace)
            * ESTIMATED_MONITOR_POINT_BYTES
            / (1024.0 * 1024.0)
        )

    def _check_memory_warning(self) -> None:
        if self.memory_warning_issued:
            return
        estimated_mb = self.estimated_memory_mb()
        if estimated_mb < self.memory_warning_mb:
            return
        self.memory_warning_issued = True
        self.memory_warning_requested.emit(estimated_mb, len(self.monitor_trace))

    def save_csv(self, path: Path) -> None:
        path = Path(path)
        x_mode = self.track_x_var.currentText()
        y_mode = self.track_quantity.currentText()

        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["# file_type", "monitor_track"])
            writer.writerow(["# timestamp_utc", utc_now_iso()])
            writer.writerow(["# x_variable", x_mode])
            writer.writerow(["# y_quantity", y_mode])
            writer.writerow(
                [
                    "# note",
                    "Captured wavelength/range values are fixed at acquisition time.",
                ]
            )
            writer.writerow(
                [
                    "timestamp_utc",
                    "elapsed_s",
                    "field_mT",
                    "power_ch1_W",
                    "power_ch2_W",
                    "intensity_target_counts",
                    "intensity_target_nm",
                    "integrated_range_counts_nm",
                    "integration_start_nm",
                    "integration_stop_nm",
                    "total_integrated_counts_nm",
                    "peak_intensity_counts",
                    "peak_wavelength_nm",
                    "signal_max_counts",
                    "signal_mean_counts",
                    "display_x",
                    "display_y",
                ]
            )

            for point in self.monitor_trace:
                x, _, _ = monitor_x(point, x_mode)
                y, _, _ = monitor_y(point, y_mode)
                writer.writerow(
                    [
                        point.timestamp_utc,
                        f"{point.elapsed_s:.9f}",
                        f"{point.field_mT:.6g}",
                        f"{point.power_ch1_W:.12e}",
                        f"{point.power_ch2_W:.12e}",
                        f"{point.intensity_target_counts:.12e}",
                        f"{point.intensity_target_nm:.6f}",
                        f"{point.integrated_range_counts_nm:.12e}",
                        f"{point.integration_start_nm:.6f}",
                        f"{point.integration_stop_nm:.6f}",
                        f"{point.total_integrated_counts_nm:.12e}",
                        f"{point.peak_intensity_counts:.12e}",
                        f"{point.peak_wavelength_nm:.6f}",
                        f"{point.signal_max_counts:.12e}",
                        f"{point.signal_mean_counts:.12e}",
                        f"{x:.12e}" if math.isfinite(x) else "",
                        f"{y:.12e}" if math.isfinite(y) else "",
                    ]
                )

    def apply_plot_style(self, settings) -> None:
        pen = (
            pg.mkPen(settings.monitor_color, width=settings.monitor_line_width)
            if settings.monitor_show_line and settings.monitor_line_width > 0
            else None
        )
        symbol = settings.symbol if settings.monitor_show_symbols else None
        self.monitor_curve.setPen(pen)
        self.monitor_curve.setSymbol(symbol)
        self.monitor_curve.setSymbolSize(int(settings.symbol_size))

        font = self.font()
        font.setPointSize(int(settings.font_size))
        for axis_name in ("bottom", "left"):
            self.monitor_plot.getAxis(axis_name).setTickFont(font)
        self.monitor_map_view.apply_font(font)

        if settings.monitor_auto_range:
            self.monitor_plot.enableAutoRange()
            self.monitor_map_view.plot.enableAutoRange()
        else:
            self.monitor_plot.disableAutoRange()
            self.monitor_map_view.plot.disableAutoRange()

    @staticmethod
    def _set_combo_text(combo: QComboBox, text: str) -> None:
        index = combo.findText(str(text))
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)

    def load_preferences(self, settings: QSettings) -> None:
        self.track_enable.setChecked(
            get_bool(settings, "monitor/tracking_enabled", self.track_enable.isChecked())
        )
        self._set_combo_text(
            self.track_quantity,
            get_str(settings, "monitor/quantity", self.track_quantity.currentText()),
        )
        self._set_combo_text(
            self.track_x_var,
            get_str(settings, "monitor/x_variable", self.track_x_var.currentText()),
        )
        self._set_combo_data(
            self.plot_mode_combo,
            get_str(settings, "monitor/plot_mode", str(self.plot_mode_combo.currentData())),
        )
        self.track_wavelength.setValue(
            get_float(settings, "monitor/target_wavelength_nm", self.track_wavelength.value())
        )
        self.track_start_nm.setValue(
            get_float(settings, "monitor/start_nm", self.track_start_nm.value())
        )
        self.track_stop_nm.setValue(
            get_float(settings, "monitor/stop_nm", self.track_stop_nm.value())
        )
        self.redraw()

    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("monitor/tracking_enabled", self.track_enable.isChecked())
        settings.setValue("monitor/quantity", self.track_quantity.currentText())
        settings.setValue("monitor/x_variable", self.track_x_var.currentText())
        settings.setValue("monitor/plot_mode", str(self.plot_mode_combo.currentData()))
        settings.setValue("monitor/target_wavelength_nm", self.track_wavelength.value())
        settings.setValue("monitor/start_nm", self.track_start_nm.value())
        settings.setValue("monitor/stop_nm", self.track_stop_nm.value())
