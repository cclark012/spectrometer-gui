from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
)

from ui.theme import ThemeManager
from ui.theme_preview import ThemePreviewWidget


class ThemePreviewDialog(QDialog):
    """Preview themes without changing the global application theme."""

    theme_selected = Signal(str)

    def __init__(
        self,
        manager: ThemeManager,
        *,
        current_theme: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.selected_theme = str(current_theme)

        self.setWindowTitle("Theme Preview")
        self.resize(760, 720)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.theme_combo = QComboBox()
        for key, label in manager.available_theme_items():
            self.theme_combo.addItem(label, key)
        index = self.theme_combo.findData(self.selected_theme)
        self.theme_combo.setCurrentIndex(max(0, index))
        self.theme_combo.currentIndexChanged.connect(self._update_preview)
        form.addRow("Theme", self.theme_combo)
        layout.addLayout(form)

        self.preview = ThemePreviewWidget(self)
        layout.addWidget(self.preview, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("Use Theme")
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self._select_theme
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_preview()

    def _update_preview(self) -> None:
        key = str(self.theme_combo.currentData())
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance()
        if application is None:
            return
        palette, stylesheet, plot_bg, plot_fg = self.manager.preview_components(
            application, key
        )
        self.preview.apply_preview(
            palette=palette,
            stylesheet=stylesheet,
            plot_background=plot_bg,
            plot_foreground=plot_fg,
        )

    def _select_theme(self) -> None:
        self.selected_theme = str(self.theme_combo.currentData())
        self.theme_selected.emit(self.selected_theme)
        self.accept()
