from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.records import SpectrometerCapabilities


class SpectrometerDetailsDialog(QDialog):
    tec_target_requested = Signal(float)
    tec_enabled_requested = Signal(bool)
    temperature_refresh_requested = Signal()

    def __init__(self, capabilities: SpectrometerCapabilities, parent=None) -> None:
        super().__init__(parent)

        self.capabilities = capabilities

        self.setWindowTitle("Spectrometer Details")
        self.resize(650, 520)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.tec_enabled_check = QCheckBox()
        self.tec_enabled_check.setEnabled(bool(capabilities.tec_supported))
        self.tec_enabled_check.toggled.connect(self.tec_enabled_requested.emit)

        self.tec_target_spin = QDoubleSpinBox()
        self.tec_target_spin.setRange(-50.0, 50.0)
        self.tec_target_spin.setDecimals(2)
        self.tec_target_spin.setValue(-10.0)
        self.tec_target_spin.setSuffix(" °C")
        self.tec_target_spin.setEnabled(bool(capabilities.tec_supported))

        self.set_tec_button = QPushButton("Set TEC Target")
        self.set_tec_button.setEnabled(bool(capabilities.tec_supported))
        self.set_tec_button.clicked.connect(
            lambda: self.tec_target_requested.emit(float(self.tec_target_spin.value()))
        )

        self.refresh_temp_button = QPushButton("Read CCD Temperature")
        self.refresh_temp_button.setEnabled(bool(capabilities.tec_supported))
        self.refresh_temp_button.clicked.connect(self.temperature_refresh_requested.emit)

        form.addRow("TEC supported", str(bool(capabilities.tec_supported)))
        form.addRow("TEC enabled", self.tec_enabled_check)
        form.addRow("TEC target", self.tec_target_spin)
        form.addRow("", self.set_tec_button)
        form.addRow("", self.refresh_temp_button)

        form.addRow("Device averaging supported", str(bool(capabilities.device_averaging_supported)))

        layout.addLayout(form)

        text = QTextEdit()
        text.setReadOnly(True)

        lines = [
            f"Model: {capabilities.model}",
            f"Serial: {capabilities.serial_number}",
            f"Pixels: {capabilities.pixels}",
            f"Max intensity: {capabilities.max_intensity}",
            f"Integration limits: {capabilities.integration_time_min_us} - {capabilities.integration_time_max_us} us",
            "",
            "Features:",
        ]

        for feature in capabilities.features:
            lines.append(f"  {feature}")

        lines.append("")
        lines.append("Feature methods:")

        for feature, methods in capabilities.feature_methods.items():
            lines.append(f"{feature}:")
            for method in methods:
                lines.append(f"  {method}")

        text.setPlainText("\n".join(lines))
        layout.addWidget(text, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
