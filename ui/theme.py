from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QPixmap
from PySide6.QtWidgets import QApplication

from ui.theme_catalog import (
    HIGH_CONTRAST_DARK,
    NORD_DARK,
    SYSTEM_THEME,
    VISUAL_STUDIO_DARK,
    VISUAL_STUDIO_LIGHT,
    ThemeCatalog,
    custom_theme_directory,
)
from ui.theme_models import ThemeDefinition
from ui.theme_render import build_palette, render_stylesheet

THEME_SETTINGS_KEY = "display/theme_name"
LEGACY_THEME_SETTINGS_KEY = "ui/theme"


class ThemeManager:
    """Apply built-in and user-defined semantic themes."""

    def __init__(
        self,
        app: QApplication,
        theme_dir: Path | None = None,
        custom_directory: Path | None = None,
    ) -> None:
        self.theme_dir = (
            Path(theme_dir)
            if theme_dir is not None
            else Path(__file__).resolve().parent / "themes"
        )
        self.catalog = ThemeCatalog(custom_directory or custom_theme_directory())
        self._system_style_name = app.style().name()
        self._system_palette = QPalette(app.palette())
        self._system_stylesheet = app.styleSheet()
        self._system_preview_pixmap = self._capture_system_preview(app)

    def _capture_system_preview(self, app: QApplication) -> QPixmap:
        """Render native widgets before the persisted application QSS is applied.

        A child widget cannot opt out of an application-level stylesheet. A
        cached native rendering is therefore the only faithful, side-effect-free
        preview after another theme is active.
        """

        from ui.theme_preview import ThemePreviewWidget

        preview = ThemePreviewWidget()
        preview.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        preview.resize(720, 620)
        preview.apply_preview(
            palette=QPalette(self._system_palette),
            stylesheet=self._system_stylesheet,
            plot_background=self._system_palette.color(
                QPalette.ColorRole.Base
            ).name(),
            plot_foreground=self._system_palette.color(
                QPalette.ColorRole.Text
            ).name(),
        )
        preview.ensurePolished()
        if preview.layout() is not None:
            preview.layout().activate()
        pixmap = preview.grab()
        preview.deleteLater()
        return pixmap

    def system_preview_pixmap(self) -> QPixmap:
        return QPixmap(self._system_preview_pixmap)

    def available_themes(self) -> tuple[str, ...]:
        return tuple(key for key, _label in self.catalog.items())

    def available_theme_items(self) -> list[tuple[str, str]]:
        return self.catalog.items()

    def definition(self, theme_name: str) -> ThemeDefinition | None:
        return self.catalog.get(theme_name)

    def apply(self, app: QApplication, theme_name: str) -> str:
        name = str(theme_name or SYSTEM_THEME)
        if name == SYSTEM_THEME:
            self._apply_system(app)
            return SYSTEM_THEME

        theme = self.catalog.get(name)
        if theme is None:
            self._apply_system(app)
            return SYSTEM_THEME

        self._apply_definition(app, theme)
        return theme.key

    def preview_components(
        self,
        app: QApplication,
        theme_name: str,
    ) -> tuple[QPalette, str, str, str]:
        theme = self.catalog.get(theme_name)
        if theme is None:
            return (
                QPalette(self._system_palette),
                self._system_stylesheet,
                self._system_palette.color(QPalette.ColorRole.Base).name(),
                self._system_palette.color(QPalette.ColorRole.Text).name(),
            )
        style_name = theme.widget_style or "Fusion"
        if app.style().name().lower() != style_name.lower():
            # Palette generation uses the current style's standard palette, but a
            # preview subtree cannot safely replace the application style. The
            # semantic colors remain representative.
            pass
        return (
            build_palette(app, theme),
            render_stylesheet(theme, theme_dir=self.theme_dir),
            theme.plot_background,
            theme.plot_foreground,
        )

    def _apply_definition(self, app: QApplication, theme: ThemeDefinition) -> None:
        app.setStyle(theme.widget_style or "Fusion")
        app.setPalette(build_palette(app, theme))
        app.setStyleSheet(render_stylesheet(theme, theme_dir=self.theme_dir))
        pg.setConfigOptions(
            background=theme.plot_background,
            foreground=theme.plot_foreground,
            antialias=True,
        )

    def _apply_system(self, app: QApplication) -> None:
        app.setStyle(self._system_style_name)
        app.setPalette(QPalette(self._system_palette))
        app.setStyleSheet(self._system_stylesheet)
        pg.setConfigOptions(
            background=self._system_palette.color(QPalette.ColorRole.Base).name(),
            foreground=self._system_palette.color(QPalette.ColorRole.Text).name(),
            antialias=True,
        )

    def save_custom_theme(
        self,
        theme: ThemeDefinition,
        *,
        overwrite: bool = False,
    ) -> ThemeDefinition:
        return self.catalog.save_custom(theme, overwrite=overwrite)


__all__ = [
    "HIGH_CONTRAST_DARK",
    "NORD_DARK",
    "SYSTEM_THEME",
    "THEME_SETTINGS_KEY",
    "LEGACY_THEME_SETTINGS_KEY",
    "VISUAL_STUDIO_DARK",
    "VISUAL_STUDIO_LIGHT",
    "ThemeManager",
]
