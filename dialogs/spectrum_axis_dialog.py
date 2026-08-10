from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QVBoxLayout,
)

from core.settings import PlotStyleSettings


class SpectrumAxisDialog(QDialog):
    def __init__(self, settings: PlotStyleSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Spectrum Axis Limits")
        self._settings = settings

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.auto_range_check = QCheckBox()
        self.auto_range_check.setChecked(bool(settings.spectrum_auto_range))
        self.auto_range_check.toggled.connect(self._set_manual_controls_enabled)
        self.x_min = self._make_spin(settings.spectrum_x_min, " nm")
        self.x_max = self._make_spin(settings.spectrum_x_max, " nm")
        self.y_min = self._make_spin(settings.spectrum_y_min)
        self.y_max = self._make_spin(settings.spectrum_y_max)
        form.addRow("Auto range", self.auto_range_check)
        form.addRow("X min", self.x_min)
        form.addRow("X max", self.x_max)
        form.addRow("Y min", self.y_min)
        form.addRow("Y max", self.y_max)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._set_manual_controls_enabled(self.auto_range_check.isChecked())

    @staticmethod
    def _make_spin(value: float, suffix: str = "") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1.0e12, 1.0e12)
        spin.setDecimals(3)
        spin.setValue(float(value))
        spin.setSuffix(suffix)
        return spin

    def _set_manual_controls_enabled(self, auto_range: bool) -> None:
        enabled = not bool(auto_range)
        for control in (self.x_min, self.x_max, self.y_min, self.y_max):
            control.setEnabled(enabled)

    def accept(self) -> None:
        auto_range = bool(self.auto_range_check.isChecked())
        x_min, x_max = float(self.x_min.value()), float(self.x_max.value())
        y_min, y_max = float(self.y_min.value()), float(self.y_max.value())

        if not auto_range:
            if x_max <= x_min:
                self.x_max.setFocus()
                return
            if y_max <= y_min:
                self.y_max.setFocus()
                return

        self._settings.spectrum_auto_range = auto_range
        self._settings.spectrum_x_min = x_min
        self._settings.spectrum_x_max = x_max
        self._settings.spectrum_y_min = y_min
        self._settings.spectrum_y_max = y_max
        super().accept()
