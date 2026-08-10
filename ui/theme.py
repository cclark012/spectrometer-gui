from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_visual_studio_dark(app: QApplication) -> None:
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1E1E1E"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#D4D4D4"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#252526"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2D2D30"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#D4D4D4"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#2D2D30"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#D4D4D4"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#007ACC"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))

    app.setPalette(palette)

    pg.setConfigOptions(
        background="#1E1E1E",
        foreground="#D4D4D4",
        antialias=True,
    )
