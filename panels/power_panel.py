from __future__ import annotations

import math
from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.preferences import get_bool, get_int, get_str
from core.records import PowerSnapshot, PowerTracePoint
from core.units import format_power_w


class PowerPanel(QWidget):
    clear_requested = Signal()
    save_requested = Signal()
    details_requested = Signal()
    mode_changed = Signal(str)
    wavelength_set_requested = Signal(int)
    auto_wavelength_changed = Signal(bool)
    redrawn = Signal()

    def __init__(self, *, max_points: int = 600, parent=None) -> None:
        super().__init__(parent)

        self.power_trace: deque[PowerTracePoint] = deque(maxlen=int(max_points))

        layout = QVBoxLayout(self)

        mode_row = QHBoxLayout()

        mode_row.addWidget(QLabel("Mode"))

        self.power_mode_combo = QComboBox()
        self.power_mode_combo.addItem("Live readings", "live")
        self.power_mode_combo.addItem("Spectra only", "spectra_only")
        self.power_mode_combo.currentIndexChanged.connect(self._emit_mode_changed)

        mode_row.addWidget(self.power_mode_combo)
        layout.addLayout(mode_row)

        self.power_detail_label = QLabel("ch1: --\nch2: --")
        self.power_detail_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.power_detail_label)

        self.power_stats_label = QLabel(
            "N: 0\n"
            "Mean: --\n"
            "Std dev: --\n"
            "RMS stability: --\n"
            "Peak-to-peak: --"
        )
        self.power_stats_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.power_stats_label)

        self.power_plot = pg.PlotWidget()
        self.power_plot.setLabel("bottom", "Time (s)")
        self.power_plot.setLabel("left", "Power ch1 (W)")
        self.power_curve = self.power_plot.plot()
        for axis_name in ["bottom", "left"]:
            axis = self.power_plot.getAxis(axis_name)
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)
        layout.addWidget(self.power_plot)

        self.clear_button = QPushButton("Clear Power Trace")
        self.clear_button.clicked.connect(lambda _checked=False: self.clear_requested.emit())
        layout.addWidget(self.clear_button)

        self.save_button = QPushButton("Save Power Trace")
        self.save_button.clicked.connect(lambda _checked=False: self.save_requested.emit())
        layout.addWidget(self.save_button)

        self.details_button = QPushButton("Details")
        self.details_button.clicked.connect(lambda _checked=False: self.details_requested.emit())
        layout.addWidget(self.details_button)

        wavelength_row = QHBoxLayout()

        self.auto_wavelength_check = QCheckBox("Auto λ")
        self.auto_wavelength_check.setChecked(True)
        self.auto_wavelength_check.toggled.connect(self.auto_wavelength_changed.emit)

        self.pm_wavelength_spin = QSpinBox()
        self.pm_wavelength_spin.setRange(190, 2000)
        self.pm_wavelength_spin.setSingleStep(1)
        self.pm_wavelength_spin.setValue(532)
        self.pm_wavelength_spin.setSuffix(" nm")
        self.pm_wavelength_spin.setMinimumWidth(95)

        self.set_pm_wavelength_button = QPushButton("Set λ")
        self.set_pm_wavelength_button.clicked.connect(self._emit_wavelength_requested)

        wavelength_row.addWidget(self.auto_wavelength_check)
        wavelength_row.addWidget(self.pm_wavelength_spin)
        wavelength_row.addWidget(self.set_pm_wavelength_button)

        layout.addLayout(wavelength_row)

        self._plot_dirty = False
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(200)
        self._redraw_timer.timeout.connect(self._redraw_if_dirty)
        self._redraw_timer.start()

    def set_max_points(self, max_points: int) -> None:
        max_points = int(max_points)

        old_points = list(self.power_trace)[-max_points:]
        self.power_trace = deque(old_points, maxlen=max_points)

        self.redraw()

    def set_current_power(self, power: PowerSnapshot) -> None:
        ch1 = power.powers_w[0] if len(power.powers_w) >= 1 else float("nan")
        ch2 = power.powers_w[1] if len(power.powers_w) >= 2 else float("nan")

        self.power_detail_label.setText(
            "ch1: "
            + format_power_w(ch1)
            + "\nch2: "
            + format_power_w(ch2)
        )

    def append_point(self, point: PowerTracePoint) -> None:
        self.power_trace.append(point)
        self._plot_dirty = True

    def _redraw_if_dirty(self) -> None:
        if not self._plot_dirty:
            return
        self._plot_dirty = False
        self.redraw()
        self.redrawn.emit()

    def clear(self) -> None:
        self.power_trace.clear()
        self._plot_dirty = False
        self.redraw()

    def set_redraw_interval_ms(self, interval_ms: int) -> None:
        self._redraw_timer.setInterval(max(20, int(interval_ms)))

    def points(self) -> list[PowerTracePoint]:
        return list(self.power_trace)

    def redraw(self) -> None:
        x = []
        y = []

        for point in self.power_trace:
            if len(point.powers_w) < 1:
                continue

            p = float(point.powers_w[0])

            if not math.isfinite(p):
                continue

            x.append(float(point.elapsed_s))
            y.append(p)

        self.power_curve.setData(x, y)
        self._update_statistics(y)

    def _update_statistics(self, powers_w: list[float]) -> None:
        if not powers_w:
            self.power_stats_label.setText(
                "N: 0\n"
                "Mean: --\n"
                "Std dev: --\n"
                "RMS stability: --\n"
                "Peak-to-peak: --"
            )
            return

        arr = np.asarray(powers_w, dtype=float)
        arr = arr[np.isfinite(arr)]

        if arr.size == 0:
            self.power_stats_label.setText(
                "N: 0\n"
                "Mean: --\n"
                "Std dev: --\n"
                "RMS stability: --\n"
                "Peak-to-peak: --"
            )
            return

        mean_w = float(np.mean(arr))
        std_w = float(np.std(arr, ddof=0))
        min_w = float(np.min(arr))
        max_w = float(np.max(arr))

        if mean_w != 0.0 and math.isfinite(mean_w):
            rms_stability_percent = 100.0 * std_w / abs(mean_w)
            peak_to_peak_percent = 100.0 * (max_w - min_w) / abs(mean_w)
        else:
            rms_stability_percent = float("nan")
            peak_to_peak_percent = float("nan")

        self.power_stats_label.setText(
            f"N: {arr.size}\n"
            f"Mean: {format_power_w(mean_w)}\n"
            f"Std dev: {format_power_w(std_w)}\n"
            f"RMS stability: {rms_stability_percent:.5g} %\n"
            f"Peak-to-peak: {peak_to_peak_percent:.5g} %"
        )

    def _emit_mode_changed(self) -> None:
        mode = str(self.power_mode_combo.currentData())
        self.mode_changed.emit(mode)

    def set_mode(self, mode: str) -> None:
        index = self.power_mode_combo.findData(str(mode))

        if index < 0:
            index = self.power_mode_combo.findData("live")

        self.power_mode_combo.blockSignals(True)
        self.power_mode_combo.setCurrentIndex(index)
        self.power_mode_combo.blockSignals(False)

    def _emit_wavelength_requested(self) -> None:
        self.wavelength_set_requested.emit(int(self.pm_wavelength_spin.value()))

    def set_power_meter_wavelength_nm(self, wavelength_nm: int) -> None:
        self.pm_wavelength_spin.blockSignals(True)
        self.pm_wavelength_spin.setValue(int(wavelength_nm))
        self.pm_wavelength_spin.blockSignals(False)

    def auto_wavelength_enabled(self) -> bool:
        return bool(self.auto_wavelength_check.isChecked())

    def set_auto_wavelength_enabled(self, enabled: bool) -> None:
        self.auto_wavelength_check.setChecked(bool(enabled))

    def apply_plot_style(self, settings) -> None:
        pen = (
            pg.mkPen(settings.power_color, width=settings.power_line_width)
            if settings.power_show_line and settings.power_line_width > 0
            else None
        )

        symbol = settings.symbol if settings.power_show_symbols else None

        self.power_curve.setPen(pen)
        self.power_curve.setSymbol(symbol)
        self.power_curve.setSymbolSize(int(settings.symbol_size))

        font = self.font()
        font.setPointSize(int(settings.font_size))

        for axis_name in ["bottom", "left"]:
            axis = self.power_plot.getAxis(axis_name)
            axis.setTickFont(font)

            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)

        self.power_plot.setLabel("bottom", "Time (s)")
        self.power_plot.setLabel("left", "Power ch1 (W)")

        if settings.power_auto_range:
            self.power_plot.enableAutoRange()
        else:
            self.power_plot.disableAutoRange()

    def load_preferences(self, settings: QSettings) -> None:
        self.set_mode(get_str(settings, "power/mode", "live"))

        self.set_auto_wavelength_enabled(
            get_bool(settings, "power/auto_wavelength", True)
        )

        self.set_power_meter_wavelength_nm(
            get_int(settings, "power/wavelength_nm", self.pm_wavelength_spin.value())
        )

    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("power/mode", str(self.power_mode_combo.currentData()))
        settings.setValue("power/auto_wavelength", self.auto_wavelength_check.isChecked())
        settings.setValue("power/wavelength_nm", self.pm_wavelength_spin.value())
