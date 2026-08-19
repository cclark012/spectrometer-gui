from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from core.settings import DisplaySettings
from dialogs.theme_editor_dialog import ThemeEditorDialog
from ui.theme import ThemeManager
from ui.theme_preview import ThemePreviewWidget


class DisplaySettingsDialog(QDialog):
    """Edit live-acquisition pacing, plot redraw rates, and startup theme."""

    def __init__(self, settings: DisplaySettings, theme_manager: ThemeManager, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Display Settings")
        self.resize(720, 700)
        self._settings = replace(settings)
        self._theme_manager = theme_manager

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.live_gap_ms = self._milliseconds_spin(
            settings.live_acquisition_gap_ms,
            minimum=0,
            maximum=60_000,
        )
        self.live_gap_rate = QLabel()
        form.addRow(
            "Live acquisition gap",
            self._with_rate_label(self.live_gap_ms, self.live_gap_rate, gap=True),
        )

        self.spectrum_redraw_ms = self._milliseconds_spin(
            settings.spectrum_redraw_interval_ms,
            minimum=20,
            maximum=10_000,
        )
        self.spectrum_rate = QLabel()
        form.addRow(
            "Spectrum redraw",
            self._with_rate_label(self.spectrum_redraw_ms, self.spectrum_rate),
        )

        self.monitor_redraw_ms = self._milliseconds_spin(
            settings.monitor_redraw_interval_ms,
            minimum=20,
            maximum=10_000,
        )
        self.monitor_rate = QLabel()
        form.addRow(
            "Monitor redraw",
            self._with_rate_label(self.monitor_redraw_ms, self.monitor_rate),
        )

        self.power_redraw_ms = self._milliseconds_spin(
            settings.power_redraw_interval_ms,
            minimum=20,
            maximum=10_000,
        )
        self.power_rate = QLabel()
        form.addRow(
            "Power redraw",
            self._with_rate_label(self.power_redraw_ms, self.power_rate),
        )

        self.theme_combo = QComboBox()
        self.customize_theme_button = QPushButton("Clone / Customize…")
        self.customize_theme_button.clicked.connect(self._customize_theme)
        theme_row = QHBoxLayout()
        theme_row.addWidget(self.theme_combo, stretch=1)
        theme_row.addWidget(self.customize_theme_button)
        form.addRow("Theme", theme_row)
        self._reload_theme_items(settings.theme_name)

        root.addLayout(form)

        note = QLabel(
            "Redraw intervals limit display updates only; acquired spectra and monitor "
            "points are still retained. Theme changes take effect after restarting the GUI."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.theme_preview = ThemePreviewWidget(self)
        root.addWidget(self.theme_preview, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for spin, label, gap in (
            (self.live_gap_ms, self.live_gap_rate, True),
            (self.spectrum_redraw_ms, self.spectrum_rate, False),
            (self.monitor_redraw_ms, self.monitor_rate, False),
            (self.power_redraw_ms, self.power_rate, False),
        ):
            spin.valueChanged.connect(
                lambda value, target=label, is_gap=gap: self._update_rate_label(
                    target,
                    int(value),
                    gap=is_gap,
                )
            )
            self._update_rate_label(label, spin.value(), gap=gap)

        self.theme_combo.currentIndexChanged.connect(self._update_theme_preview)
        self._update_theme_preview()

    def _reload_theme_items(self, selected: str) -> None:
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for key, display_name in self._theme_manager.available_theme_items():
            self.theme_combo.addItem(display_name, key)
        index = self.theme_combo.findData(str(selected))
        self.theme_combo.setCurrentIndex(index if index >= 0 else 0)
        self.theme_combo.blockSignals(False)

    def _update_theme_preview(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        palette, stylesheet, plot_background, plot_foreground = (
            self._theme_manager.preview_components(
                app,
                str(self.theme_combo.currentData()),
            )
        )
        self.theme_preview.apply_preview(
            palette=palette,
            stylesheet=stylesheet,
            plot_background=plot_background,
            plot_foreground=plot_foreground,
        )

    def _customize_theme(self) -> None:
        dialog = ThemeEditorDialog(
            self._theme_manager,
            base_theme=str(self.theme_combo.currentData()),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.saved_theme is None:
            return
        self._reload_theme_items(dialog.saved_theme.key)
        self._update_theme_preview()

    @staticmethod
    def _milliseconds_spin(value: int, *, minimum: int, maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(int(minimum), int(maximum))
        spin.setSingleStep(10)
        spin.setSuffix(" ms")
        spin.setValue(int(value))
        return spin

    @staticmethod
    def _with_rate_label(spin: QSpinBox, label: QLabel, *, gap: bool = False) -> QHBoxLayout:
        del gap
        row = QHBoxLayout()
        row.addWidget(spin)
        row.addWidget(label)
        row.addStretch(1)
        return row

    @staticmethod
    def _update_rate_label(label: QLabel, interval_ms: int, *, gap: bool) -> None:
        interval_ms = int(interval_ms)
        if gap:
            label.setText("immediate chaining" if interval_ms == 0 else f"+{interval_ms} ms")
            return
        label.setText(f"{1000.0 / max(1, interval_ms):.2f} Hz max")

    def settings(self) -> DisplaySettings:
        return replace(
            self._settings,
            live_acquisition_gap_ms=int(self.live_gap_ms.value()),
            spectrum_redraw_interval_ms=int(self.spectrum_redraw_ms.value()),
            monitor_redraw_interval_ms=int(self.monitor_redraw_ms.value()),
            power_redraw_interval_ms=int(self.power_redraw_ms.value()),
            theme_name=str(self.theme_combo.currentData()),
        )
