from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QVBoxLayout, QWidget

from core.records import SpectrumRecord
from core.settings import PlotStyleSettings


class SpectrumPanel(QWidget):
    """Throttled spectrum display with persistent auto/manual view limits."""

    def __init__(self, *, redraw_interval_ms: int = 200, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Wavelength (nm)")
        self.plot.setLabel("left", "Intensity (counts)")
        for axis_name in ("bottom", "left"):
            axis = self.plot.getAxis(axis_name)
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)
        self.curve = self.plot.plot()
        layout.addWidget(self.plot)

        self._settings = PlotStyleSettings()
        self._pending_record: SpectrumRecord | None = None
        self._plot_dirty = False

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(max(20, int(redraw_interval_ms)))
        self._redraw_timer.timeout.connect(self._redraw_if_dirty)
        self._redraw_timer.start()

    def set_redraw_interval_ms(self, interval_ms: int) -> None:
        self._redraw_timer.setInterval(max(20, int(interval_ms)))

    def queue_record(self, record: SpectrumRecord) -> None:
        self._pending_record = record
        self._plot_dirty = True

    def show_arrays(self, wavelengths_nm, intensities_counts) -> None:
        self._pending_record = None
        self._plot_dirty = False
        self.curve.setData(
            np.asarray(wavelengths_nm, dtype=float),
            np.asarray(intensities_counts, dtype=float),
        )
        self._apply_range()

    def clear(self) -> None:
        self._pending_record = None
        self._plot_dirty = False
        self.curve.setData([], [])

    def _redraw_if_dirty(self) -> None:
        if not self._plot_dirty or self._pending_record is None:
            return
        self._plot_dirty = False
        record = self._pending_record
        self.curve.setData(record.wavelengths_nm, record.intensities_counts)
        self._apply_range()

    def apply_style(self, settings: PlotStyleSettings) -> None:
        self._settings = settings
        pen = (
            pg.mkPen(settings.spectrum_color, width=settings.spectrum_line_width)
            if settings.spectrum_show_line and settings.spectrum_line_width > 0
            else None
        )
        self.curve.setPen(pen)
        self.curve.setSymbol(settings.symbol if settings.spectrum_show_symbols else None)
        self.curve.setSymbolSize(int(settings.symbol_size))

        font = self.font()
        font.setPointSize(int(settings.font_size))
        for axis_name in ("bottom", "left"):
            axis = self.plot.getAxis(axis_name)
            axis.setTickFont(font)
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)
        self._apply_range()

    def set_auto_range(self, enabled: bool) -> None:
        self._settings.spectrum_auto_range = bool(enabled)
        self._apply_range()

    def auto_range_now(self) -> None:
        self.plot.enableAutoRange()
        self.plot.autoRange()
        if not self._settings.spectrum_auto_range:
            self.plot.disableAutoRange()

    def apply_manual_range(self) -> None:
        settings = self._settings
        self.plot.disableAutoRange()
        if settings.spectrum_x_max > settings.spectrum_x_min:
            self.plot.setXRange(
                settings.spectrum_x_min,
                settings.spectrum_x_max,
                padding=0.0,
            )
        if settings.spectrum_y_max > settings.spectrum_y_min:
            self.plot.setYRange(
                settings.spectrum_y_min,
                settings.spectrum_y_max,
                padding=0.0,
            )

    def _apply_range(self) -> None:
        if self._settings.spectrum_auto_range:
            self.plot.enableAutoRange()
            self.plot.autoRange()
        else:
            self.apply_manual_range()

    def current_view_range(self) -> tuple[float, float, float, float]:
        (x_min, x_max), (y_min, y_max) = self.plot.viewRange()
        return float(x_min), float(x_max), float(y_min), float(y_max)
