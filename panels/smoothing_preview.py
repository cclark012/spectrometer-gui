from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

from processing.smoothing import smoothing_kernel


class KernelPreview(QWidget):
    """Small theme-transparent smoothing-kernel preview."""

    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._window = "none"
        self._width = 1
        self.setFixedSize(72, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click for kernel weights and frequency response.")

    def set_kernel(self, window: str, width: int) -> None:
        self._window = str(window)
        self._width = int(width)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        palette = self.palette()
        painter.setPen(QPen(palette.mid().color(), 1.0))
        painter.drawRoundedRect(rect, 3.0, 3.0)
        try:
            weights = smoothing_kernel(self._window, self._width)
        except ValueError:
            weights = np.ones(1, dtype=float)
        if weights.size == 1:
            painter.setPen(QPen(palette.text().color(), 1.5))
            painter.drawLine(
                QPointF(rect.left() + 5.0, rect.center().y()),
                QPointF(rect.right() - 5.0, rect.center().y()),
            )
            return
        maximum = max(float(np.max(weights)), np.finfo(float).eps)
        path = QPainterPath()
        for index, value in enumerate(weights):
            x = rect.left() + 4.0 + (rect.width() - 8.0) * index / (weights.size - 1)
            y = rect.bottom() - 3.0 - (rect.height() - 6.0) * float(value) / maximum
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        accent = QColor(palette.highlight().color())
        painter.setPen(QPen(accent, 1.6))
        painter.drawPath(path)


class SmoothingPreviewDialog(QDialog):
    """Detailed normalized kernel and Fourier-response display."""

    def __init__(self, window: str, width: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Smoothing Window")
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        weights = smoothing_kernel(window, width)
        convention = (
            "FWHM in samples" if str(window).lower() == "gaussian" else "samples"
        )
        description = QLabel(
            f"{str(window).title()} — width {int(width)} {convention}; "
            "weights normalized to unit sum."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        kernel_plot = pg.PlotWidget()
        kernel_plot.setLabel("bottom", "Kernel sample")
        kernel_plot.setLabel("left", "Normalized weight")
        kernel_plot.plot(np.arange(weights.size), weights, pen="y", symbol="o")
        layout.addWidget(kernel_plot)

        fft_size = max(1024, 1 << int(np.ceil(np.log2(max(2, weights.size * 8)))))
        response = np.abs(np.fft.rfft(weights, n=fft_size))
        response /= max(float(response[0]), np.finfo(float).eps)
        response_db = 20.0 * np.log10(np.maximum(response, 1.0e-6))
        frequencies = np.fft.rfftfreq(fft_size)
        response_plot = pg.PlotWidget()
        response_plot.setLabel("bottom", "Spatial frequency", units="cycles/sample")
        response_plot.setLabel("left", "Magnitude", units="dB")
        response_plot.setYRange(-120.0, 3.0)
        response_plot.plot(frequencies, response_db, pen="c")
        layout.addWidget(response_plot)
