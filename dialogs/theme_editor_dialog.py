from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyleFactory,
    QVBoxLayout,
    QWidget,
)

from ui.theme import ThemeManager
from ui.theme_catalog import SYSTEM_THEME, sanitize_theme_key
from ui.theme_models import ThemeDefinition
from ui.theme_preview import ThemePreviewWidget

_COLOR_FIELDS: tuple[tuple[str, str], ...] = (
    ("window", "Window"),
    ("panel", "Panel"),
    ("input_background", "Input background"),
    ("alternate_background", "Alternate background"),
    ("border", "Border"),
    ("border_hover", "Hover border"),
    ("text", "Text"),
    ("muted_text", "Muted text"),
    ("disabled_text", "Disabled text"),
    ("accent", "Accent"),
    ("accent_hover", "Accent hover"),
    ("selection", "Selection"),
    ("error", "Error"),
    ("warning", "Warning"),
    ("success", "Success"),
    ("plot_background", "Plot background"),
    ("plot_foreground", "Plot foreground"),
    ("plot_grid", "Plot grid"),
    ("checkbox_border", "Checkbox border"),
)


class _ColorButton(QPushButton):
    def __init__(self, value: str, parent=None) -> None:
        super().__init__(parent)
        self._value = "#000000"
        self.clicked.connect(self._choose)
        self.set_value(value)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        color = QColor(str(value))
        if not color.isValid():
            color = QColor("#000000")
        self._value = color.name().upper()
        contrast = "#000000" if color.lightnessF() > 0.55 else "#FFFFFF"
        self.setText(self._value)
        self.setStyleSheet(
            f"background-color: {self._value}; color: {contrast}; "
            "border: 1px solid #808080; padding: 4px;"
        )

    def _choose(self) -> None:
        color = QColorDialog.getColor(QColor(self._value), self, "Choose Color")
        if color.isValid():
            self.set_value(color.name())
            parent = self.window()
            update = getattr(parent, "update_preview", None)
            if callable(update):
                update()


class ThemeEditorDialog(QDialog):
    """Edit semantic theme roles and save them as a user JSON theme."""

    def __init__(
        self,
        manager: ThemeManager,
        *,
        base_theme: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.saved_theme: ThemeDefinition | None = None
        self._color_buttons: dict[str, _ColorButton] = {}

        self.setWindowTitle("Theme Editor")
        self.resize(1050, 760)

        root = QVBoxLayout(self)
        splitter = QSplitter()
        root.addWidget(splitter, stretch=1)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        form = QFormLayout()

        self.base_combo = QComboBox()
        for key, label in manager.available_theme_items():
            if key != SYSTEM_THEME:
                self.base_combo.addItem(label, key)
        index = self.base_combo.findData(base_theme)
        self.base_combo.setCurrentIndex(max(0, index))
        self.base_combo.currentIndexChanged.connect(self._load_base)
        form.addRow("Base theme", self.base_combo)

        self.name_edit = QLineEdit("Custom Theme")
        self.name_edit.textChanged.connect(self.update_preview)
        form.addRow("Display name", self.name_edit)

        self.key_edit = QLineEdit("custom_theme")
        form.addRow("Theme key", self.key_edit)

        self.dark_check = QCheckBox()
        self.dark_check.toggled.connect(self.update_preview)
        form.addRow("Dark theme", self.dark_check)

        self.style_combo = QComboBox()
        self.style_combo.addItems(QStyleFactory.keys())
        self.style_combo.currentTextChanged.connect(self.update_preview)
        form.addRow("Qt widget style", self.style_combo)

        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(0, 12)
        self.radius_spin.valueChanged.connect(self.update_preview)
        form.addRow("Corner radius", self.radius_spin)

        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(0, 14)
        self.padding_spin.valueChanged.connect(self.update_preview)
        form.addRow("Control padding", self.padding_spin)
        editor_layout.addLayout(form)

        colors_widget = QWidget()
        grid = QGridLayout(colors_widget)
        for row, (field_name, label) in enumerate(_COLOR_FIELDS):
            button = _ColorButton("#000000", self)
            self._color_buttons[field_name] = button
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(button, row, 1)
        editor_layout.addWidget(colors_widget)
        editor_layout.addStretch(1)

        self.preview = ThemePreviewWidget()
        splitter.addWidget(editor)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save Theme")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(
            self._load_base
        )
        root.addWidget(buttons)

        self._load_base()

    def _load_base(self) -> None:
        key = str(self.base_combo.currentData())
        theme = self.manager.definition(key)
        if theme is None:
            return
        self.name_edit.setText(f"{theme.display_name} Custom")
        self.key_edit.setText(f"custom_{sanitize_theme_key(theme.key)}")
        self.dark_check.setChecked(theme.dark)
        style_index = self.style_combo.findText(theme.widget_style)
        if style_index >= 0:
            self.style_combo.setCurrentIndex(style_index)
        self.radius_spin.setValue(theme.corner_radius_px)
        self.padding_spin.setValue(theme.control_padding_px)
        for field_name, _label in _COLOR_FIELDS:
            self._color_buttons[field_name].set_value(getattr(theme, field_name))
        self.update_preview()

    def theme_definition(self) -> ThemeDefinition:
        colors = {
            name: button.value()
            for name, button in self._color_buttons.items()
        }
        return ThemeDefinition(
            key=sanitize_theme_key(self.key_edit.text() or self.name_edit.text()),
            display_name=self.name_edit.text().strip() or "Custom Theme",
            dark=self.dark_check.isChecked(),
            widget_style=self.style_combo.currentText() or "Fusion",
            corner_radius_px=self.radius_spin.value(),
            control_padding_px=self.padding_spin.value(),
            **colors,
        )

    def update_preview(self) -> None:
        from PySide6.QtWidgets import QApplication

        from ui.theme_render import build_palette, render_stylesheet

        app = QApplication.instance()
        if app is None:
            return
        theme = self.theme_definition()
        self.preview.apply_preview(
            palette=build_palette(app, theme),
            stylesheet=render_stylesheet(theme, theme_dir=self.manager.theme_dir),
            plot_background=theme.plot_background,
            plot_foreground=theme.plot_foreground,
        )

    def _save(self) -> None:
        theme = self.theme_definition()
        try:
            self.saved_theme = self.manager.save_custom_theme(theme)
        except FileExistsError:
            result = QMessageBox.question(
                self,
                "Overwrite Theme",
                f"A custom theme named {theme.key!r} already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return
            self.saved_theme = self.manager.save_custom_theme(theme, overwrite=True)
        except Exception as exc:
            QMessageBox.critical(self, "Save Theme Failed", str(exc))
            return
        self.accept()
