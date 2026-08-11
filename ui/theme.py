from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

SYSTEM_THEME = "system"
VISUAL_STUDIO_DARK = "visual_studio_dark"


class ThemeManager:
    """Apply repository-owned themes without requiring archived third-party code."""

    def __init__(self, theme_dir: Path | None = None) -> None:
        self.theme_dir = (
            Path(theme_dir)
            if theme_dir is not None
            else Path(__file__).resolve().parent / "themes"
        )

    @staticmethod
    def available_themes() -> tuple[str, ...]:
        return (SYSTEM_THEME, VISUAL_STUDIO_DARK)

    def apply(self, app: QApplication, theme_name: str) -> str:
        name = str(theme_name or SYSTEM_THEME)
        if name == VISUAL_STUDIO_DARK:
            self._apply_visual_studio_dark(app)
            return VISUAL_STUDIO_DARK
        self._apply_system(app)
        return SYSTEM_THEME

    @staticmethod
    def _apply_system(app: QApplication) -> None:
        app.setStyleSheet("")
        app.setPalette(app.style().standardPalette())
        pg.setConfigOptions(background="w", foreground="k", antialias=True)

    def _apply_visual_studio_dark(self, app: QApplication) -> None:
        app.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#1E1E1E"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#D4D4D4"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#252526"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2D2D30"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#252526"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#D4D4D4"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#D4D4D4"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#2D2D30"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#D4D4D4"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#007ACC"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#808080"))
        app.setPalette(palette)

        stylesheet_path = self.theme_dir / "visual_studio_dark.qss"
        stylesheet = stylesheet_path.read_text(encoding="utf-8")
        # checkmark_path = (self.theme_dir / "checkmark.svg").resolve()
        # stylesheet = stylesheet.replace("__CHECKMARK_URL__", checkmark_path.as_uri())
        app.setStyleSheet(stylesheet)
        # app.setStyleSheet(
            # stylesheet_path.read_text(encoding="utf-8")
            # if stylesheet_path.exists()
            # else ""
        # )
        pg.setConfigOptions(
            background="#1E1E1E",
            foreground="#D4D4D4",
            antialias=True,
        )
