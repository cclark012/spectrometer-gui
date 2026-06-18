# panels/scan_panel.py

from __future__ import annotations

import math

from PySide6.QtCore import Signal, QSettings # noqa
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.laser_models import PowerScanPoint
from core.preferences import get_str
from planning.power_scan import make_requested_powers_w, make_power_scan_plan, ScanPlan


_POWER_FACTORS = {
    "W": 1.0,
    "mW": 1e-3,
    "μW": 1e-6,
    "uW": 1e-6,
    "nW": 1e-9,
}


def format_power(power_w: float) -> str:
    if not math.isfinite(float(power_w)):
        return "--"

    p = float(power_w)
    ap = abs(p)

    if ap >= 1.0:
        return f"{p:.5g} W"
    if ap >= 1e-3:
        return f"{p * 1e3:.5g} mW"
    if ap >= 1e-6:
        return f"{p * 1e6:.5g} μW"
    if ap >= 1e-9:
        return f"{p * 1e9:.5g} nW"

    return f"{p:.4e} W"


class ScanPanel(QWidget):
    preview_requested = Signal()
    run_requested = Signal()
    abort_requested = Signal()
    calibration_requested = Signal()
    save_calibration_requested = Signal()
    load_calibration_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._points: list[PowerScanPoint] = []

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.basis_combo = QComboBox()
        self.basis_combo.addItem("Laser setpoint", "setpoint")
        self.basis_combo.addItem("Expected actual power", "expected_actual")

        self.spacing_combo = QComboBox()
        self.spacing_combo.addItem("Linear", "linear")
        self.spacing_combo.addItem("Logarithmic", "logarithmic")
        self.spacing_combo.addItem("Custom", "custom")
        self.spacing_combo.currentIndexChanged.connect(self._on_spacing_changed)

        self.calibration_reads = QSpinBox()
        self.calibration_reads.setRange(1, 1000)
        self.calibration_reads.setValue(3)

        self.start_power = QDoubleSpinBox()
        self.start_power.setRange(0.0, 1.0e9)
        self.start_power.setDecimals(3)
        self.start_power.setValue(1.0)
        self.start_power.setMaximumWidth(100)

        self.stop_power = QDoubleSpinBox()
        self.stop_power.setRange(0.0, 1.0e9)
        self.stop_power.setDecimals(3)
        self.stop_power.setValue(10.0)
        self.stop_power.setMaximumWidth(100)

        self.power_units = QComboBox()
        self.power_units.setMaximumWidth(72)
        self.power_units.addItems(["W", "mW", "μW", "nW"])

        power_row = QHBoxLayout()
        power_row.addWidget(self.start_power)
        power_row.addWidget(QLabel("to"))
        power_row.addWidget(self.stop_power)
        power_row.addWidget(self.power_units)

        self.n_points = QSpinBox()
        self.n_points.setRange(1, 10_000)
        self.n_points.setValue(5)
        self.n_points.setMaximumWidth(78)

        self.repeats_per_point = QSpinBox()
        self.repeats_per_point.setRange(1, 1000)
        self.repeats_per_point.setValue(1)
        self.repeats_per_point.setMaximumWidth(78)

        self.settling_time_s = QDoubleSpinBox()
        self.settling_time_s.setRange(0.0, 3600.0)
        self.settling_time_s.setDecimals(2)
        self.settling_time_s.setValue(1.0)
        self.settling_time_s.setSuffix(" s")
        self.settling_time_s.setMaximumWidth(100)

        self.enable_before_scan = QCheckBox()
        self.enable_before_scan.setChecked(True)

        self.disable_after_scan = QCheckBox()
        self.disable_after_scan.setChecked(False)

        self.autosave_scan_spectra = QCheckBox()
        self.autosave_scan_spectra.setChecked(True)

        form.addRow("Basis", self.basis_combo)
        form.addRow("Spacing", self.spacing_combo)
        form.addRow("Calibration reads/point", self.calibration_reads)
        form.addRow("Power range", power_row)
        form.addRow("Points", self.n_points)
        form.addRow("Repeats/point", self.repeats_per_point)
        form.addRow("Settling", self.settling_time_s)
        form.addRow("Enable before scan", self.enable_before_scan)
        form.addRow("Disable after scan", self.disable_after_scan)
        form.addRow("Autosave spectra", self.autosave_scan_spectra)

        layout.addLayout(form)

        self.custom_values = QTextEdit()
        self.custom_values.setPlaceholderText(
            "Custom powers, one per line, using selected units.\n"
            "Example:\n1\n2\n5\n10"
        )
        self.custom_values.setFixedHeight(90)
        layout.addWidget(self.custom_values)
        self.custom_values.setVisible(False)

        buttons = QHBoxLayout()

        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self.preview_requested.emit)

        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self.run_requested.emit)

        self.abort_button = QPushButton("Abort")
        self.abort_button.clicked.connect(self.abort_requested.emit)
        self.abort_button.setEnabled(False)
        
        self.calibration_button = QPushButton("Run Calibration")
        self.calibration_button.clicked.connect(self.calibration_requested.emit)

        self.save_calibration_button = QPushButton("Save")
        self.save_calibration_button.clicked.connect(self.save_calibration_requested.emit)

        self.load_calibration_button = QPushButton("Load")
        self.load_calibration_button.clicked.connect(self.load_calibration_requested.emit)


        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.abort_button)
        buttons.addWidget(self.calibration_button)
        buttons.addWidget(self.load_calibration_button)
        buttons.addWidget(self.save_calibration_button)

        layout.addLayout(buttons)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setHorizontalHeaderLabels(
            [
                "Index",
                "Requested",
                "Setpoint",
                "Expected actual",
                "Filter",
            ]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        layout.addWidget(self.table, stretch=1)
        
        self.warning_label = QLabel("")
        layout.addWidget(self.warning_label)

    def _on_spacing_changed(self) -> None:
        spacing = str(self.spacing_combo.currentData())
        self.custom_values.setVisible(spacing == "custom")

    def calibration_reads_per_point(self) -> int:
        return int(self.calibration_reads.value())

    def power_factor(self) -> float:
        return _POWER_FACTORS[str(self.power_units.currentText())]

    def scan_basis(self) -> str:
        return str(self.basis_combo.currentData())

    def spacing(self) -> str:
        return str(self.spacing_combo.currentData())

    def settling_seconds(self) -> float:
        return float(self.settling_time_s.value())

    def repeats(self) -> int:
        return int(self.repeats_per_point.value())

    def should_enable_before_scan(self) -> bool:
        return bool(self.enable_before_scan.isChecked())

    def should_disable_after_scan(self) -> bool:
        return bool(self.disable_after_scan.isChecked())

    def should_autosave_scan_spectra(self) -> bool:
        return bool(self.autosave_scan_spectra.isChecked())

    def custom_powers_w(self) -> list[float]:
        factor = self.power_factor()
        values = []

        for line in self.custom_values.toPlainText().splitlines():
            text = line.strip()

            if not text:
                continue

            values.append(float(text) * factor)

        return values

    def make_plan_for_laser(
        self,
        *,
        laser_min_setpoint_w: float,
        laser_max_setpoint_w: float,
        calibration=None,
        transmission: float = 1.0,
    ) -> ScanPlan:
        factor = self.power_factor()
        spacing = self.spacing()

        requested = make_requested_powers_w(
            start_w=float(self.start_power.value()) * factor,
            stop_w=float(self.stop_power.value()) * factor,
            n_points=int(self.n_points.value()),
            spacing=spacing,
            custom_values_w=self.custom_powers_w() if spacing == "custom" else None,
        )

        return make_power_scan_plan(
            requested_powers_w=requested,
            basis=self.scan_basis(),
            laser_min_setpoint_w=float(laser_min_setpoint_w),
            laser_max_setpoint_w=float(laser_max_setpoint_w),
            calibration=calibration,
            transmission=float(transmission),
            filter_state="none",
            allow_clipping=True,
        )

    def make_points_for_laser(
        self,
        *,
        laser_min_setpoint_w: float,
        laser_max_setpoint_w: float,
        calibration=None,
        transmission: float = 1.0,
    ) -> ScanPlan:

        plan = self.make_plan_for_laser(
            laser_min_setpoint_w=float(laser_min_setpoint_w),
            laser_max_setpoint_w=float(laser_max_setpoint_w),
            calibration=calibration,
            transmission=float(transmission),
        )

        return plan.points

    def set_points(self, points: list[PowerScanPoint], warnings: list[str]) -> None:
        self._points = list(points)
        self._warnings = list(warnings or [])

        self.table.setRowCount(len(self._points))

        for row, point in enumerate(self._points):
            values = [
                str(point.index + 1),
                format_power(point.requested_power_w),
                format_power(point.setpoint_w),
                format_power(point.expected_actual_power_w),
                str(point.filter_state),
            ]

            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

        self.table.resizeColumnsToContents()

        if self._warnings:
            self.warning_label.setText(f"{len(self._warnings)} warning(s).")
            self.warning_label.setToolTip("\n".join(self._warnings))
        else:
            self.warning_label.setText("")
            self.warning_label.setToolTip("")

    def warnings(self) -> list[str]:
        return list(getattr(self, "_warnings", []))

    def points(self) -> list[PowerScanPoint]:
        return list(self._points)

    def set_running(self, running: bool) -> None:
        running = bool(running)

        self.run_button.setEnabled(not running)
        self.preview_button.setEnabled(not running)
        self.abort_button.setEnabled(running)
    
    def load_preferences(self, settings: QSettings) -> None:
        units = get_str(settings, "scan/power_units", self.power_units.currentText())
        index = self.power_units.findText(units)

        if index >= 0:
            self.power_units.setCurrentIndex(index)

    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("scan/power_units", self.power_units.currentText())
