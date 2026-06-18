
# panels/monitor_panel.py

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
import pyqtgraph as pg

try:
    import pyqtgraph.opengl as gl
    HAS_GL_3D = True
except Exception:
    gl = None
    HAS_GL_3D = False

from PySide6.QtCore import QSettings, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,  # noqa
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.preferences import get_bool, get_float, get_str
from core.records import MonitorTracePoint, SpectrumRecord
from core.time_utils import utc_now_iso

ESTIMATED_MONITOR_POINT_BYTES = 768


def label_with_units(label: str, units: str) -> str:
    return f"{label} ({units})" if units else label


class MonitorPanel(QWidget):
    save_requested = Signal()
    cleared = Signal()
    memory_warning_requested = Signal(float, int)  # estimated_mb, n_points

    def __init__(self, *, memory_warning_mb: float = 50.0, parent=None) -> None:
        super().__init__(parent)

        self.monitor_trace: list[MonitorTracePoint] = []
        self.memory_warning_mb = float(memory_warning_mb)
        self.memory_warning_issued = False

        layout = QVBoxLayout(self)

        self.monitor_stack = QStackedWidget()

        self.monitor_plot = pg.PlotWidget()
        self.monitor_plot.setLabel("bottom", "Time (s)")
        self.monitor_plot.setLabel("left", "Tracked quantity")
        self.monitor_curve = self.monitor_plot.plot()

        for axis_name in ["bottom", "left"]:
            axis = self.monitor_plot.getAxis(axis_name)
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)

        self.monitor_stack.addWidget(self.monitor_plot)

        if HAS_GL_3D:
            self.monitor_3d_container = QWidget()
            monitor_3d_layout = QVBoxLayout(self.monitor_3d_container)
            monitor_3d_layout.setContentsMargins(0, 0, 0, 0)

            self.monitor_3d_info_label = QLabel(
                "3D monitor: x = magnetic field, y = power, z = selected quantity"
            )
            self.monitor_3d_info_label.setWordWrap(True)
            monitor_3d_layout.addWidget(self.monitor_3d_info_label)

            self.monitor_3d = gl.GLViewWidget()
            self.monitor_3d.setCameraPosition(distance=2.6, elevation=24, azimuth=42)
            monitor_3d_layout.addWidget(self.monitor_3d, stretch=1)

            self.monitor_3d_scatter = gl.GLScatterPlotItem()
            self.monitor_3d.addItem(self.monitor_3d_scatter)

            self.monitor_3d_axis_items = []
            self.monitor_3d_label_items = []

            self._build_3d_static_axes()

            self.monitor_stack.addWidget(self.monitor_3d_container)

        self.monitor_map_container = QWidget()
        monitor_map_layout = QVBoxLayout(self.monitor_map_container)
        monitor_map_layout.setContentsMargins(0, 0, 0, 0)

        self.monitor_map_info_label = QLabel(
            "Field-power map: x = magnetic field, y = power ch1, color = selected quantity"
        )
        self.monitor_map_info_label.setWordWrap(True)
        monitor_map_layout.addWidget(self.monitor_map_info_label)

        self.monitor_map_plot = pg.PlotWidget()
        self.monitor_map_plot.setLabel("bottom", "Magnetic field (mT)")
        self.monitor_map_plot.setLabel("left", "Power ch1 (W)")

        for axis_name in ["bottom", "left"]:
            axis = self.monitor_map_plot.getAxis(axis_name)
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)

        self.monitor_map_scatter = pg.ScatterPlotItem()
        self.monitor_map_plot.addItem(self.monitor_map_scatter)

        monitor_map_layout.addWidget(self.monitor_map_plot, stretch=1)
        self.monitor_stack.addWidget(self.monitor_map_container)

        layout.addWidget(self.monitor_stack)

        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()

        self.plot_mode_combo = QComboBox()
        self.plot_mode_combo.addItem("2D monitor", "2d")
        self.plot_mode_combo.addItem("Field-power map", "map2d")

        if HAS_GL_3D:
            self.plot_mode_combo.addItem("3D: field / power / quantity", "3d")

        self.plot_mode_combo.currentIndexChanged.connect(self.redraw)
        form.addRow("Plot mode", self.plot_mode_combo)

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
        self.track_quantity.currentTextChanged.connect(self.redraw)

        self.track_x_var = QComboBox()
        self.track_x_var.addItems(["Time", "Power ch1", "Magnetic field"])
        self.track_x_var.currentTextChanged.connect(self.redraw)

        self.track_wavelength = QDoubleSpinBox()
        self.track_wavelength.setRange(0.0, 2000.0)
        self.track_wavelength.setDecimals(1)
        self.track_wavelength.setSingleStep(10.0)
        self.track_wavelength.setValue(550.0)
        self.track_wavelength.setSuffix(" nm")

        self.track_start_nm = QDoubleSpinBox()
        self.track_start_nm.setRange(0.0, 2000.0)
        self.track_start_nm.setDecimals(1)
        self.track_start_nm.setSingleStep(10.0)
        self.track_start_nm.setValue(450.0)
        self.track_start_nm.setSuffix(" nm")

        self.track_stop_nm = QDoubleSpinBox()
        self.track_stop_nm.setRange(0.0, 2000.0)
        self.track_stop_nm.setDecimals(1)
        self.track_stop_nm.setSingleStep(10.0)
        self.track_stop_nm.setValue(750.0)
        self.track_stop_nm.setSuffix(" nm")

        form.addRow("Track spectra", self.track_enable)
        form.addRow("Quantity", self.track_quantity)
        form.addRow("Versus", self.track_x_var)
        form.addRow("Wavelength", self.track_wavelength)
        form.addRow("Start", self.track_start_nm)
        form.addRow("Stop", self.track_stop_nm)

        controls_layout.addLayout(form)

        button_layout = QVBoxLayout()

        self.clear_button = QPushButton("Clear Monitor")
        self.clear_button.clicked.connect(self.clear)

        self.save_button = QPushButton("Save Monitor")
        self.save_button.clicked.connect(self.save_requested.emit)

        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.save_button)
        button_layout.addStretch(1)

        controls_layout.addLayout(button_layout)

        layout.addWidget(controls)
        
        self._plot_old = False
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(200)
        self._redraw_timer.timeout.connect(self._redraw_if_old)
        self._redraw_timer.start()


    def tracking_enabled(self) -> bool:
        return bool(self.track_enable.isChecked())

    def points(self) -> list[MonitorTracePoint]:
        return list(self.monitor_trace)

    def has_points(self) -> bool:
        return bool(self.monitor_trace)

    def clear(self) -> None:
        self.monitor_trace.clear()
        self.memory_warning_issued = False
        self.monitor_curve.setData([], [])
        self.cleared.emit()

    def add_record(self, record: SpectrumRecord) -> None:
        point = self._make_monitor_trace_point(record)
        self.monitor_trace.append(point)

        self._check_memory_warning()
        self.redraw()

    def _plot_mode(self) -> str:
        return str(self.plot_mode_combo.currentData())

    def _normalize_01(self, values: list[float]) -> np.ndarray:
        arr = np.asarray(values, dtype=float)

        if arr.size == 0:
            return arr

        finite = np.isfinite(arr)

        if not np.any(finite):
            return np.zeros_like(arr)

        amin = float(np.min(arr[finite]))
        amax = float(np.max(arr[finite]))

        if amax == amin:
            out = np.zeros_like(arr)
            out[finite] = 0.5
            return out

        out = (arr - amin) / (amax - amin)
        out[~finite] = 0.0
        return out

    def _format_range_value(self, value: float, units: str) -> str:
        if not math.isfinite(float(value)):
            return "--"

        value = float(value)

        if units == "W":
            if abs(value) >= 1e-3:
                return f"{value * 1e3:.3g} mW"
            if abs(value) >= 1e-6:
                return f"{value * 1e6:.3g} uW"
            if abs(value) >= 1e-9:
                return f"{value * 1e9:.3g} nW"
            return f"{value:.3e} W"

        if units:
            return f"{value:.3g} {units}"

        return f"{value:.3g}"

    def _finite_min_max(self, values: list[float]) -> tuple[float, float]:
        arr = np.asarray(values, dtype=float)
        arr = arr[np.isfinite(arr)]

        if arr.size == 0:
            return float("nan"), float("nan")

        return float(np.min(arr)), float(np.max(arr))

    def _update_3d_labels(
        self,
        *,
        field_values: list[float],
        power_values_w: list[float],
        quantity_values: list[float],
        quantity_label: str,
        quantity_units: str,
    ) -> None:
        if not HAS_GL_3D:
            return

        # Remove old dynamic labels, but keep static axes and static labels.
        dynamic_items = getattr(self, "monitor_3d_dynamic_label_items", [])
        self._clear_3d_items(dynamic_items)
        self.monitor_3d_dynamic_label_items = dynamic_items

        field_min, field_max = self._finite_min_max(field_values)
        power_min, power_max = self._finite_min_max(power_values_w)
        q_min, q_max = self._finite_min_max(quantity_values)

        text_color = (230, 230, 230, 230)

        # Tick labels: min/max for each normalized axis.
        labels = [
            ((0.00, -0.08, -0.02), self._format_range_value(field_min, "mT")),
            ((1.00, -0.08, -0.02), self._format_range_value(field_max, "mT")),

            ((-0.12, 0.00, -0.02), self._format_range_value(power_min, "W")),
            ((-0.12, 1.00, -0.02), self._format_range_value(power_max, "W")),

            ((-0.12, -0.08, 0.00), self._format_range_value(q_min, quantity_units)),
            ((-0.12, -0.08, 1.00), self._format_range_value(q_max, quantity_units)),
        ]

        for pos, text in labels:
            dynamic_items.append(
                self._make_3d_text(pos=pos, text=text, color=text_color, size=9)
            )

        # Update the human-readable overlay label.
        self.monitor_3d_info_label.setText(
            "3D monitor: "
            f"x = magnetic field [{self._format_range_value(field_min, 'mT')} to {self._format_range_value(field_max, 'mT')}], " # noqa
            f"y = power ch1 [{self._format_range_value(power_min, 'W')} to {self._format_range_value(power_max, 'W')}], " # noqa
            f"z = {quantity_label} [{self._format_range_value(q_min, quantity_units)} to {self._format_range_value(q_max, quantity_units)}]" # noqa
        )

    def _clear_3d_items(self, items: list) -> None:
        if not HAS_GL_3D:
            return

        for item in items:
            try:
                self.monitor_3d.removeItem(item)
            except Exception:
                pass

        items.clear()

    def _make_3d_text(self, *, pos, text: str, color=(230, 230, 230, 255), size: int = 11):
        font = QFont("Arial", int(size))

        item = gl.GLTextItem(
            pos=pos,
            text=str(text),
            color=color,
            font=font,
        )

        self.monitor_3d.addItem(item)
        return item

    def _add_3d_line(self, points, *, color=(0.8, 0.8, 0.8, 0.85), width: float = 1.5):
        item = gl.GLLinePlotItem(
            pos=np.asarray(points, dtype=float),
            color=color,
            width=float(width),
            antialias=True,
            mode="line_strip",
        )

        self.monitor_3d.addItem(item)
        return item

    def _build_3d_static_axes(self) -> None:
        if not HAS_GL_3D:
            return

        self._clear_3d_items(self.monitor_3d_axis_items)
        self._clear_3d_items(self.monitor_3d_label_items)

        # Normalized 3D plotting cube:
        # x = field, y = power, z = selected monitor quantity.
        x0, x1 = 0.0, 1.0
        y0, y1 = 0.0, 1.0
        z0, z1 = 0.0, 1.0

        axis_color = (0.86, 0.86, 0.86, 0.9) # noqa
        grid_color = (0.35, 0.35, 0.35, 0.35)

        # Main axes from origin.
        self.monitor_3d_axis_items.append(
            self._add_3d_line([(x0, y0, z0), (x1, y0, z0)], color=(0.9, 0.45, 0.45, 1.0), width=2.5)
        )
        self.monitor_3d_axis_items.append(
            self._add_3d_line([(x0, y0, z0), (x0, y1, z0)], color=(0.45, 0.9, 0.45, 1.0), width=2.5)
        )
        self.monitor_3d_axis_items.append(
            self._add_3d_line([(x0, y0, z0), (x0, y0, z1)], color=(0.45, 0.65, 1.0, 1.0), width=2.5)
        )

        # Cube edges.
        cube_edges = [
            [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z0)],
            [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1)],
            [(x0, y0, z0), (x0, y0, z1)],
            [(x1, y0, z0), (x1, y0, z1)],
            [(x1, y1, z0), (x1, y1, z1)],
            [(x0, y1, z0), (x0, y1, z1)],
        ]

        for edge in cube_edges:
            self.monitor_3d_axis_items.append(
                self._add_3d_line(edge, color=grid_color, width=1.0)
            )

        # Light base grid on z=0.
        for v in np.linspace(0.25, 0.75, 3):
            self.monitor_3d_axis_items.append(
                self._add_3d_line([(v, y0, z0), (v, y1, z0)], color=grid_color, width=0.75)
            )
            self.monitor_3d_axis_items.append(
                self._add_3d_line([(x0, v, z0), (x1, v, z0)], color=grid_color, width=0.75)
            )

        # Static axis-end labels. Dynamic numeric labels are updated in _update_3d_labels().
        self.monitor_3d_label_items.append(
            self._make_3d_text(pos=(1.08, -0.04, 0.0), text="Field", color=(255, 150, 150, 255), size=11) # noqa
        )
        self.monitor_3d_label_items.append(
            self._make_3d_text(pos=(-0.08, 1.08, 0.0), text="Power", color=(150, 255, 150, 255), size=11) # noqa
        )
        self.monitor_3d_label_items.append(
            self._make_3d_text(pos=(-0.08, -0.05, 1.08), text="Signal", color=(150, 190, 255, 255), size=11) # noqa
        )

    def _make_monitor_trace_point(self, record: SpectrumRecord) -> MonitorTracePoint:
        wl = np.asarray(record.wavelengths_nm, dtype=float)
        y = np.asarray(record.intensities_counts, dtype=float)

        elapsed_s = float(record.timestamp_s)

        # Prefer time relative to the application if MainWindow supplies it later.
        # MainWindow can call set_application_t0(), but absolute perf_counter-derived
        # values are still internally consistent.
        if hasattr(self, "_application_t0"):
            elapsed_s = float(record.timestamp_s - self._application_t0)

        target_nm = float(self.track_wavelength.value())
        intensity_target = float(np.interp(target_nm, wl, y)) if wl.size else float("nan")

        start_nm = min(float(self.track_start_nm.value()), float(self.track_stop_nm.value()))
        stop_nm = max(float(self.track_start_nm.value()), float(self.track_stop_nm.value()))

        mask = (wl >= start_nm) & (wl <= stop_nm)

        if np.count_nonzero(mask) >= 2:
            integrated_range = float(np.trapezoid(y[mask], wl[mask]))
        else:
            integrated_range = float("nan")

        if wl.size >= 2 and y.size >= 2:
            total_integrated = float(np.trapezoid(y, wl))
        else:
            total_integrated = float("nan")

        finite_mask = np.isfinite(y)

        if np.any(finite_mask):
            finite_y = y[finite_mask]
            finite_wl = wl[finite_mask]

            peak_index = int(np.argmax(finite_y))
            peak_intensity = float(finite_y[peak_index])
            peak_wavelength = float(finite_wl[peak_index])

            signal_mean = float(np.mean(finite_y))
            signal_max = float(np.max(finite_y))
        else:
            peak_intensity = float("nan")
            peak_wavelength = float("nan")
            signal_mean = float("nan")
            signal_max = float("nan")

        record_signal_max = float(getattr(record, "signal_max_counts", float("nan")))
        if math.isfinite(record_signal_max):
            signal_max = record_signal_max

        return MonitorTracePoint(
            timestamp_utc=record.timestamp_utc,
            elapsed_s=elapsed_s,

            field_mT=float(record.field_value),

            power_ch1_W=float(record.mean_power_w(0)),
            power_ch2_W=float(record.mean_power_w(1)),

            intensity_target_counts=intensity_target,
            intensity_target_nm=target_nm,

            integrated_range_counts_nm=integrated_range,
            integration_start_nm=start_nm,
            integration_stop_nm=stop_nm,

            total_integrated_counts_nm=total_integrated,

            peak_intensity_counts=peak_intensity,
            peak_wavelength_nm=peak_wavelength,

            signal_max_counts=signal_max,
            signal_mean_counts=signal_mean,
        )

    def _monitor_x_from_point(self, point: MonitorTracePoint) -> tuple[float, str, str]:
        mode = self.track_x_var.currentText()

        if mode == "Time":
            return point.elapsed_s, "Time", "s"

        if mode == "Power ch1":
            return point.power_ch1_W, "Power ch1", "W"

        if mode == "Magnetic field":
            return point.field_mT, "Magnetic field", "mT"

        return point.elapsed_s, "Time", "s"

    def _monitor_y_from_point(self, point: MonitorTracePoint) -> tuple[float, str, str]:
        mode = self.track_quantity.currentText()

        if mode == "Intensity at captured wavelength":
            return (
                point.intensity_target_counts,
                "Intensity at captured wavelength",
                "counts",
            )

        if mode == "Integrated captured range":
            return (
                point.integrated_range_counts_nm,
                "Integrated captured range",
                "counts nm",
            )

        if mode == "Total integrated intensity":
            return (
                point.total_integrated_counts_nm,
                "Total integrated intensity",
                "counts nm",
            )

        if mode == "Intensity at captured wavelength / power ch1":
            return (
                self._safe_divide(point.intensity_target_counts, point.power_ch1_W),
                "Intensity at captured wavelength / power ch1",
                "counts/W",
            )

        if mode == "Integrated captured range / power ch1":
            return (
                self._safe_divide(point.integrated_range_counts_nm, point.power_ch1_W),
                "Integrated captured range / power ch1",
                "counts nm/W",
            )

        if mode == "Total integrated intensity / power ch1":
            return (
                self._safe_divide(point.total_integrated_counts_nm, point.power_ch1_W),
                "Total integrated intensity / power ch1",
                "counts nm/W",
            )

        if mode == "Peak intensity":
            return point.peak_intensity_counts, "Peak intensity", "counts"

        if mode == "Peak wavelength":
            return point.peak_wavelength_nm, "Peak wavelength", "nm"

        if mode == "Signal max":
            return point.signal_max_counts, "Signal max", "counts"

        if mode == "Signal mean":
            return point.signal_mean_counts, "Signal mean", "counts"

        if mode == "Power ch1":
            return point.power_ch1_W, "Power ch1", "W"

        return float("nan"), "Tracked quantity", ""

    @staticmethod
    def _safe_divide(numerator: float, denominator: float) -> float:
        if not math.isfinite(float(numerator)):
            return float("nan")

        if not math.isfinite(float(denominator)) or float(denominator) == 0.0:
            return float("nan")

        return float(numerator) / float(denominator)

    def _redraw_3d(self) -> None:
        if not HAS_GL_3D:
            return

        fields_mT = []
        powers_w = []
        quantities = []

        quantity_label = "Signal"
        quantity_units = ""

        for point in self.monitor_trace:
            q, quantity_label, quantity_units = self._monitor_y_from_point(point)

            field = float(point.field_mT)
            power_w = float(point.power_ch1_W)

            if not math.isfinite(field):
                continue
            if not math.isfinite(power_w):
                continue
            if not math.isfinite(q):
                continue

            fields_mT.append(field)
            powers_w.append(power_w)
            quantities.append(float(q))

        if not fields_mT:
            self.monitor_3d_scatter.setData(pos=np.empty((0, 3)))
            self._update_3d_labels(
                field_values=[],
                power_values_w=[],
                quantity_values=[],
                quantity_label=quantity_label,
                quantity_units=quantity_units,
            )
            return

        # Normalize into a unit cube for display.
        x = self._normalize_01(fields_mT)
        y = self._normalize_01(powers_w)
        z = self._normalize_01(quantities)

        pos = np.column_stack([x, y, z])

        # Color by z value for extra visual information.
        z_norm = self._normalize_01(quantities)

        colors = np.zeros((pos.shape[0], 4), dtype=float)
        colors[:, 0] = 0.25 + 0.60 * z_norm
        colors[:, 1] = 0.70
        colors[:, 2] = 1.00 - 0.45 * z_norm
        colors[:, 3] = 0.88

        self.monitor_3d_scatter.setData(
            pos=pos,
            color=colors,
            size=8,
            pxMode=True,
        )

        self._update_3d_labels(
            field_values=fields_mT,
            power_values_w=powers_w,
            quantity_values=quantities,
            quantity_label=quantity_label,
            quantity_units=quantity_units,
        )

    def _redraw_2d(self) -> None:
        x_values = []
        y_values = []

        x_label = "X"
        x_units = ""
        y_label = "Y"
        y_units = ""

        for point in self.monitor_trace:
            x, x_label, x_units = self._monitor_x_from_point(point)
            y, y_label, y_units = self._monitor_y_from_point(point)

            if not math.isfinite(x) or not math.isfinite(y):
                continue

            x_values.append(float(x))
            y_values.append(float(y))

        self.monitor_curve.setData(x_values, y_values)

        self.monitor_plot.setLabel("bottom", label_with_units(x_label, x_units))
        self.monitor_plot.setLabel("left", label_with_units(y_label, y_units))

    def _redraw_2d_map(self) -> None:
        fields = []
        powers_w = []
        quantities = []

        quantity_label = "Signal"
        quantity_units = ""

        for point in self.monitor_trace:
            q, quantity_label, quantity_units = self._monitor_y_from_point(point)

            field = float(point.field_mT)
            power_w = float(point.power_ch1_W)

            if not math.isfinite(field):
                continue
            if not math.isfinite(power_w):
                continue
            if not math.isfinite(q):
                continue

            fields.append(field)
            powers_w.append(power_w)
            quantities.append(float(q))

        if not fields:
            self.monitor_map_scatter.setData([])
            self.monitor_map_info_label.setText(
                "Field-power map: no finite monitor points"
            )
            return

        q_norm = self._normalize_01(quantities)

        spots = []

        for field, power_w, q, zn in zip(fields, powers_w, quantities, q_norm): # noqa
            # Pastel-to-warm color scale.
            r = int(60 + 180 * zn)
            g = int(170 - 70 * zn)
            b = int(255 - 120 * zn)

            spots.append(
                {
                    "pos": (field, power_w),
                    "brush": pg.mkBrush(r, g, b, 210),
                    "pen": pg.mkPen(40, 40, 40, 100),
                    "size": 8,
                    "data": q,
                }
            )

        self.monitor_map_scatter.setData(spots)

        q_min, q_max = self._finite_min_max(quantities)
        field_min, field_max = self._finite_min_max(fields)
        power_min, power_max = self._finite_min_max(powers_w)

        self.monitor_map_info_label.setText(
            "Field-power map: "
            f"x = field [{field_min:.4g} to {field_max:.4g} mT], "
            f"y = power [{self._format_range_value(power_min, 'W')} to {self._format_range_value(power_max, 'W')}], " # noqa
            f"color = {quantity_label} [{self._format_range_value(q_min, quantity_units)} to {self._format_range_value(q_max, quantity_units)}]" # noqa
        )

    def _redraw_if_old(self) -> None:
        if not self._plot_old:
            return
        self._plot_old = False
        self.redraw()

    def redraw(self) -> None:
        mode = self._plot_mode()

        if mode == "3d" and HAS_GL_3D:
            self.monitor_stack.setCurrentWidget(self.monitor_3d_container)
            self._redraw_3d()
        elif mode == "map2d":
            self.monitor_stack.setCurrentWidget(self.monitor_map_container)
            self._redraw_2d_map()
        else:
            self.monitor_stack.setCurrentWidget(self.monitor_plot)
            self._redraw_2d()

    def set_application_t0(self, app_t0: float) -> None:
        self._application_t0 = float(app_t0)

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

        with path.open("w", newline="") as f:
            writer = csv.writer(f)

            writer.writerow(["# file_type", "monitor_track"])
            writer.writerow(["# timestamp_utc", utc_now_iso()])
            writer.writerow(["# x_variable", self.track_x_var.currentText()])
            writer.writerow(["# y_quantity", self.track_quantity.currentText()])
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
                x, _, _ = self._monitor_x_from_point(point)
                y, _, _ = self._monitor_y_from_point(point)

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

        for axis_name in ["bottom", "left"]:
            axis = self.monitor_plot.getAxis(axis_name)
            axis.setTickFont(font)

            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)

        if settings.monitor_auto_range:
            self.monitor_plot.enableAutoRange()

    def _set_combo_text(self, combo, text: str) -> None:
        index = combo.findText(str(text))

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

        self.track_wavelength.setValue(
            get_float(settings, "monitor/target_wavelength_nm", self.track_wavelength.value())
        )
        self.track_start_nm.setValue(
            get_float(settings, "monitor/start_nm", self.track_start_nm.value())
        )
        self.track_stop_nm.setValue(
            get_float(settings, "monitor/stop_nm", self.track_stop_nm.value())
        )

    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("monitor/tracking_enabled", self.track_enable.isChecked())
        settings.setValue("monitor/quantity", self.track_quantity.currentText())
        settings.setValue("monitor/x_variable", self.track_x_var.currentText())
        settings.setValue("monitor/target_wavelength_nm", self.track_wavelength.value())
        settings.setValue("monitor/start_nm", self.track_start_nm.value())
        settings.setValue("monitor/stop_nm", self.track_stop_nm.value())
