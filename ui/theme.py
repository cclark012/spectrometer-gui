from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

SYSTEM_THEME = "system"
VISUAL_STUDIO_DARK = "visual_studio_dark"
THEME_SETTINGS_KEY = "display/theme_name"
LEGACY_THEME_SETTINGS_KEY = "ui/theme"


def custom_theme_directory() -> Path:
    root = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation
            .AppConfigLocation
        )
    )

    path = root / "themes"
    path.mkdir(parents=True, exist_ok=True)

    return path


class ThemeManager:
    def __init__(
        self,
        app: QApplication,
        theme_dir: Path | None = None,
    ) -> None:
        self.theme_dir = (
            theme_dir
            if theme_dir is not None
            else Path(__file__).resolve().parent
            / "themes"
        )

        self._system_style_name = (
            app.style().name()
        )
        self._system_palette = QPalette(
            app.palette()
        )
        self._system_stylesheet = (
            app.styleSheet()
        )

    @staticmethod
    def available_themes() -> tuple[str, ...]:
        return (SYSTEM_THEME, VISUAL_STUDIO_DARK)

    def apply(
        self,
        app: QApplication,
        theme_name: str,
    ) -> str:
        name = str(theme_name or SYSTEM_THEME)

        if name not in self.available_themes():
            name = SYSTEM_THEME

        if name == VISUAL_STUDIO_DARK:
            self._apply_visual_studio_dark(app)
            return VISUAL_STUDIO_DARK

        self._apply_system(app)
        return SYSTEM_THEME

    def _apply_system(
        self,
        app: QApplication,
    ) -> None:
        app.setStyle(self._system_style_name)
        app.setPalette(
            QPalette(self._system_palette)
        )
        app.setStyleSheet(
            self._system_stylesheet
        )

    def _apply_visual_studio_dark(self, app: QApplication) -> None:
        app.setStyle("Fusion")
        palette = QPalette(app.style().standardPalette())
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

        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor("#707070"),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.WindowText,
            QColor("#707070"),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor("#707070"),
        )

        app.setPalette(palette)

        stylesheet_path = (
            self.theme_dir
            / "visual_studio_dark.qss"
        )

        if not stylesheet_path.exists():
            app.setStyleSheet("")
        else:
            stylesheet = stylesheet_path.read_text(
                encoding="utf-8"
            )

            checkmark_path = (
                self.theme_dir / "checkmark.svg"
            ).resolve()

            stylesheet = stylesheet.replace(
                "__CHECKMARK_URL__",
                checkmark_path.as_posix(),
            )

            app.setStyleSheet(stylesheet)

        pg.setConfigOptions(
            background="#1E1E1E",
            foreground="#D4D4D4",
            antialias=True,
        )
