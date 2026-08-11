from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from core.settings import DisplaySettings


class PerformanceSettingsDialog(QDialog):
    """Edit the performance monitor without changing plot redraw intervals."""

    def __init__(self, settings: DisplaySettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Performance Monitor Settings")
        self.resize(450, 270)
        self._settings = replace(settings)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.enabled = QCheckBox()
        self.enabled.setChecked(bool(settings.performance_enabled))

        self.report_interval_ms = QSpinBox()
        self.report_interval_ms.setRange(100, 60_000)
        self.report_interval_ms.setSingleStep(100)
        self.report_interval_ms.setSuffix(" ms")
        self.report_interval_ms.setValue(int(settings.performance_report_interval_ms))

        self.probe_interval_ms = QSpinBox()
        self.probe_interval_ms.setRange(20, 10_000)
        self.probe_interval_ms.setSingleStep(10)
        self.probe_interval_ms.setSuffix(" ms")
        self.probe_interval_ms.setValue(int(settings.event_loop_probe_interval_ms))

        self.rate_window_s = QDoubleSpinBox()
        self.rate_window_s.setRange(0.5, 120.0)
        self.rate_window_s.setDecimals(1)
        self.rate_window_s.setSingleStep(0.5)
        self.rate_window_s.setSuffix(" s")
        self.rate_window_s.setValue(float(settings.performance_rate_window_s))

        form.addRow("Show performance indicator", self.enabled)
        form.addRow("Status update interval", self.report_interval_ms)
        form.addRow("Event-loop probe interval", self.probe_interval_ms)
        form.addRow("Rate averaging window", self.rate_window_s)
        root.addLayout(form)

        note = QLabel(
            "The monitor reports acquisition rate, actual plot redraw rates, and Qt "
            "event-loop lag. Faster probing adds a small amount of GUI overhead."
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

    def settings(self) -> DisplaySettings:
        return replace(
            self._settings,
            performance_enabled=bool(self.enabled.isChecked()),
            performance_report_interval_ms=int(self.report_interval_ms.value()),
            event_loop_probe_interval_ms=int(self.probe_interval_ms.value()),
            performance_rate_window_s=float(self.rate_window_s.value()),
        )
