from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from panels.plot_options import PaddedViewBox, apply_plot_options, color_with_alpha
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from core.records import SpectrumRecord
from core.settings import PlotStyleSettings


class SpectrumPanel(QWidget):
    """Throttled spectrum display with persistent auto/manual view limits."""
    redrawn = Signal()

    def __init__(self, *, redraw_interval_ms: int = 200, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget(viewBox=PaddedViewBox())
        self.plot.setLabel("bottom", "Wavelength (nm)")
        self.plot.setLabel("left", "Intensity (counts)")
        for axis_name in ("bottom", "left"):
            axis = self.plot.getAxis(axis_name)
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)
        self.plot.addLegend()
        self.curve = self.plot.plot(name="Spectrum / smoothed")
        self.unsmoothed_curve = self.plot.plot(name="Unsmoothed", pen=pg.mkPen("#e69f00", width=1))
        self.background_curve = self.plot.plot(name="Background (capture counts)", pen=pg.mkPen("#cc79a7", width=1))
        self.curve.setZValue(20)
        self.unsmoothed_curve.setZValue(10)
        self.background_curve.setZValue(0)
        self._smoothed_visible = False
        self.unsmoothed_curve.hide()
        self.background_curve.hide()
        layout.addWidget(self.plot)

        self._settings = PlotStyleSettings()
        self._style_curves()
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
        self.unsmoothed_curve.hide()
        self._smoothed_visible = False
        self._style_curves()
        self.curve.setData(
            np.asarray(wavelengths_nm, dtype=float),
            np.asarray(intensities_counts, dtype=float),
        )
        self._apply_range()

    def clear(self) -> None:
        self._pending_record = None
        self._plot_dirty = False
        self.curve.setData([], [])
        self.unsmoothed_curve.hide()

    def _redraw_if_dirty(self) -> None:
        if not self._plot_dirty or self._pending_record is None:
            return
        self._plot_dirty = False
        record = self._pending_record
        self.curve.setData(record.wavelengths_nm, record.intensities_counts)
        unsmoothed = record.unsmoothed_intensities_counts
        if unsmoothed is None and not record.background_subtracted:
            unsmoothed = record.raw_intensities_counts
        show = record.smoothing_window != "none" and record.boxcar_width > 1 and unsmoothed is not None
        self._smoothed_visible = show
        self._style_curves()
        self.unsmoothed_curve.setVisible(show)
        if show:
            self.unsmoothed_curve.setData(record.wavelengths_nm, unsmoothed)
        self._apply_range()
        self.redrawn.emit()

    def show_background(self, background, visible: bool) -> None:
        self.background_curve.setVisible(visible and background is not None)
        if background is not None:
            self.background_curve.setData(background.wavelengths_nm, background.counts_per_s * background.integration_ms / 1000.0)

    def apply_style(self, settings: PlotStyleSettings) -> None:
        self._settings = settings
        self._style_curves()
        apply_plot_options(self.plot, settings, "spectrum")

        font = self.font()
        font.setPointSize(int(settings.font_size))
        for axis_name in ("bottom", "left"):
            axis = self.plot.getAxis(axis_name)
            axis.setTickFont(font)
            if hasattr(axis, "enableAutoSIPrefix"):
                axis.enableAutoSIPrefix(False)
        self._apply_range()

    def _style_curves(self) -> None:
        settings = self._settings
        prefix = "smoothed" if self._smoothed_visible else "spectrum"
        color = color_with_alpha(getattr(settings, prefix + "_color"), getattr(settings, prefix + "_alpha"))
        width = getattr(settings, prefix + "_line_width")
        self.curve.setPen(pg.mkPen(color, width=width) if settings.spectrum_show_line and width > 0 else None)
        self.curve.setSymbol(settings.symbol if settings.spectrum_show_symbols else None)
        self.curve.setSymbolSize(settings.symbol_size)
        self.curve.setSymbolPen(color)
        self.curve.setSymbolBrush(color)
        for name, curve in (("unsmoothed", self.unsmoothed_curve), ("background", self.background_curve)):
            width = getattr(settings, name + "_line_width")
            color = color_with_alpha(getattr(settings, name + "_color"), getattr(settings, name + "_alpha"))
            curve.setPen(pg.mkPen(color, width=width) if width > 0 else None)

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
