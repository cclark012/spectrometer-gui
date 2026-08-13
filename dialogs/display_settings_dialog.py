from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from core.settings import DisplaySettings
from ui.theme import ThemeManager


class DisplaySettingsDialog(QDialog):
    """Edit live-acquisition pacing, plot redraw rates, and startup theme."""

    def __init__(self, settings: DisplaySettings, theme_manager: ThemeManager, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Display Settings")
        self.resize(470, 300)
        self._settings = replace(settings)

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
        for key, display_name in theme_manager.available_theme_items():
            self.theme_combo.addItem(display_name, key)
        index = self.theme_combo.findData(settings.theme_name)
        self.theme_combo.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("Theme", self.theme_combo)

        root.addLayout(form)

        note = QLabel(
            "Redraw intervals limit display updates only; acquired spectra and monitor "
            "points are still retained. Theme changes take effect after restarting the GUI."
        )
        note.setWordWrap(True)
        root.addWidget(note)

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
