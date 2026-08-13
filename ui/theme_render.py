from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ui.theme_models import ThemeDefinition


def build_palette(app: QApplication, theme: ThemeDefinition) -> QPalette:
    palette = QPalette(app.style().standardPalette())
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    palette.setColor(role.Window, QColor(theme.window))
    palette.setColor(role.WindowText, QColor(theme.text))
    palette.setColor(role.Base, QColor(theme.input_background))
    palette.setColor(role.AlternateBase, QColor(theme.alternate_background))
    palette.setColor(role.ToolTipBase, QColor(theme.panel))
    palette.setColor(role.ToolTipText, QColor(theme.text))
    palette.setColor(role.Text, QColor(theme.text))
    palette.setColor(role.Button, QColor(theme.input_background))
    palette.setColor(role.ButtonText, QColor(theme.text))
    palette.setColor(role.BrightText, QColor("#FFFFFF"))
    palette.setColor(role.Highlight, QColor(theme.accent))
    palette.setColor(role.HighlightedText, QColor("#FFFFFF" if theme.dark else theme.text))
    palette.setColor(role.PlaceholderText, QColor(theme.muted_text))

    for color_role in (role.Text, role.WindowText, role.ButtonText):
        palette.setColor(group.Disabled, color_role, QColor(theme.disabled_text))

    return palette


def _checkmark_url(theme_dir: Path | None) -> str:
    if theme_dir is None:
        return ""
    path = (Path(theme_dir) / "checkmark.svg").resolve()
    return path.as_posix() if path.exists() else ""


def render_stylesheet(
    theme: ThemeDefinition,
    *,
    theme_dir: Path | None = None,
) -> str:
    checkmark = _checkmark_url(theme_dir)
    checked_image = f'image: url("{checkmark}");' if checkmark else ""
    radius = max(0, int(theme.corner_radius_px))
    padding = max(0, int(theme.control_padding_px))

    return f"""
QWidget {{
    color: {theme.text};
    background-color: {theme.window};
    selection-background-color: {theme.selection};
    selection-color: {theme.text};
}}
QMainWindow, QDialog {{
    background-color: {theme.window};
}}
QDockWidget, QGroupBox, QTabWidget::pane {{
    background-color: {theme.panel};
    border: 1px solid {theme.border};
}}
QGroupBox {{
    margin-top: 8px;
    padding-top: 8px;
    border-radius: {radius}px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox,
QComboBox, QTableView, QTableWidget, QListView, QTreeView {{
    background-color: {theme.input_background};
    border: 1px solid {theme.border};
    border-radius: {radius}px;
    padding: {padding}px;
}}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QSpinBox:hover,
QDoubleSpinBox:hover, QComboBox:hover, QTableView:hover, QTableWidget:hover {{
    border-color: {theme.border_hover};
}}
QPushButton {{
    background-color: {theme.input_background};
    border: 1px solid {theme.border};
    border-radius: {radius}px;
    padding: {padding}px {padding + 3}px;
}}
QPushButton:hover {{
    border-color: {theme.border_hover};
    background-color: {theme.alternate_background};
}}
QPushButton:pressed {{
    background-color: {theme.selection};
}}
QPushButton#primaryButton {{
    background-color: {theme.accent};
    border-color: {theme.accent_hover};
    color: #FFFFFF;
}}
QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {theme.disabled_text};
}}
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {theme.checkbox_border};
    border-radius: 2px;
    background-color: {theme.input_background};
}}
QCheckBox::indicator:hover {{
    border-color: {theme.border_hover};
    background-color: {theme.alternate_background};
}}
QCheckBox::indicator:checked {{
    background-color: {theme.accent};
    border-color: {theme.accent_hover};
    {checked_image}
}}
QCheckBox::indicator:disabled {{
    border-color: {theme.disabled_text};
    background-color: {theme.panel};
}}
QHeaderView::section {{
    background-color: {theme.panel};
    border: 0;
    border-right: 1px solid {theme.border};
    border-bottom: 1px solid {theme.border};
    padding: {padding}px;
}}
QTabBar::tab {{
    background-color: {theme.panel};
    border: 1px solid {theme.border};
    padding: {padding}px {padding + 4}px;
}}
QTabBar::tab:selected {{
    background-color: {theme.selection};
    border-bottom-color: {theme.accent};
}}
QMenuBar, QMenu, QToolBar, QStatusBar {{
    background-color: {theme.panel};
}}
QMenu::item:selected {{ background-color: {theme.selection}; }}
QToolTip {{
    color: {theme.text};
    background-color: {theme.panel};
    border: 1px solid {theme.border};
}}
QProgressBar {{
    border: 1px solid {theme.border};
    border-radius: {radius}px;
    text-align: center;
    background-color: {theme.input_background};
}}
QProgressBar::chunk {{ background-color: {theme.accent}; }}
QScrollBar {{ background-color: {theme.panel}; }}
QScrollBar::handle {{
    background-color: {theme.border_hover};
    border-radius: {radius}px;
}}
""".strip()
