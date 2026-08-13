from __future__ import annotations

from PySide6.QtCore import QSettings, Signal
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
from core.preferences import get_bool, get_float, get_int, get_str
from core.units import format_power_w
from planning.power_scan import ScanPlan, make_power_scan_plan, make_requested_powers_w


class ScanPanel(QWidget):
    preview_requested = Signal()
    run_requested = Signal()
    abort_requested = Signal()
    calibration_requested = Signal()
    save_calibration_requested = Signal()
    load_calibration_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._running = False
        self._spectrometer_available = False
        self._power_meter_available = False
        self._lasers_available = False

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_form())

        self.custom_values = QTextEdit()
        self.custom_values.setPlaceholderText(
            "Custom powers, one per line, using selected units.\nExample:\n1\n2\n5\n10"
        )
        self.custom_values.setFixedHeight(90)
        self.custom_values.setVisible(False)
        layout.addWidget(self.custom_values)
        layout.addLayout(self._build_buttons())

        self.table = self._build_table()
        layout.addWidget(self.table, stretch=1)
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

    def _build_form(self) -> QFormLayout:
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

        self.start_power = self._make_power_spin(1.0)
        self.stop_power = self._make_power_spin(10.0)
        self.power_units = QComboBox()
        self.power_units.addItem("W", 1.0)
        self.power_units.addItem("mW", 1e-3)
        self.power_units.addItem("μW", 1e-6)
        self.power_units.addItem("nW", 1e-9)
        self.power_units.setMaximumWidth(72)

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
        return form

    @staticmethod
    def _make_power_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 1.0e9)
        spin.setDecimals(3)
        spin.setValue(float(value))
        spin.setMaximumWidth(100)
        return spin

    def _build_buttons(self) -> QHBoxLayout:
        buttons = QHBoxLayout()
        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(
            lambda _checked=False: self.preview_requested.emit()
        )
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(
            lambda _checked=False: self.run_requested.emit()
        )
        self.abort_button = QPushButton("Abort")
        self.abort_button.clicked.connect(
            lambda _checked=False: self.abort_requested.emit()
        )
        self.abort_button.setEnabled(False)
        self.calibration_button = QPushButton("Run Calibration")
        self.calibration_button.clicked.connect(
            lambda _checked=False: self.calibration_requested.emit()
        )
        self.load_calibration_button = QPushButton("Load")
        self.load_calibration_button.clicked.connect(
            lambda _checked=False: self.load_calibration_requested.emit()
        )
        self.save_calibration_button = QPushButton("Save")
        self.save_calibration_button.clicked.connect(
            lambda _checked=False: self.save_calibration_requested.emit()
        )

        for button in (
            self.preview_button,
            self.run_button,
            self.abort_button,
            self.calibration_button,
            self.load_calibration_button,
            self.save_calibration_button,
        ):
            buttons.addWidget(button)
        return buttons

    @staticmethod
    def _build_table() -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(
            ["Index", "Requested", "Setpoint", "Expected actual", "Filter"]
        )
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(22)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        return table

    def _on_spacing_changed(self) -> None:
        self.custom_values.setVisible(self.spacing() == "custom")

    def calibration_reads_per_point(self) -> int:
        return int(self.calibration_reads.value())

    def power_factor(self) -> float:
        return float(self.power_units.currentData())

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

    def requested_powers_w(self) -> list[float]:
        spacing = self.spacing()
        return make_requested_powers_w(
            start_w=float(self.start_power.value()) * self.power_factor(),
            stop_w=float(self.stop_power.value()) * self.power_factor(),
            n_points=int(self.n_points.value()),
            spacing=spacing,
            custom_values_w=self.custom_powers_w() if spacing == "custom" else None,
        )

    def custom_powers_w(self) -> list[float]:
        factor = self.power_factor()
        values: list[float] = []
        for line_number, line in enumerate(self.custom_values.toPlainText().splitlines(), 1):
            text = line.strip()
            if not text:
                continue
            try:
                values.append(float(text) * factor)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid custom power on line {line_number}: {text!r}"
                ) from exc
        return values

    def make_plan_for_laser(
        self,
        *,
        laser_min_setpoint_w: float,
        laser_max_setpoint_w: float,
        calibration=None,
        transmission: float = 1.0,
        filter_state: str = "none",
        allow_clipping: bool = True,
    ) -> ScanPlan:
        return make_power_scan_plan(
            requested_powers_w=self.requested_powers_w(),
            basis=self.scan_basis(),
            laser_min_setpoint_w=float(laser_min_setpoint_w),
            laser_max_setpoint_w=float(laser_max_setpoint_w),
            calibration=calibration,
            transmission=float(transmission),
            filter_state=str(filter_state),
            allow_clipping=bool(allow_clipping),
        )

    def make_calibration_plan_for_laser(
        self,
        *,
        laser_min_setpoint_w: float,
        laser_max_setpoint_w: float,
    ) -> ScanPlan:
        """Create a calibration plan that always sweeps laser setpoints."""

        return make_power_scan_plan(
            requested_powers_w=self.requested_powers_w(),
            basis="setpoint",
            laser_min_setpoint_w=float(laser_min_setpoint_w),
            laser_max_setpoint_w=float(laser_max_setpoint_w),
            calibration=None,
            transmission=1.0,
            filter_state="none",
            allow_clipping=True,
        )

    def set_points(
        self,
        points: list[PowerScanPoint],
        warnings: list[str] | None = None,
    ) -> None:
        point_list = list(points)
        warning_list = list(warnings or [])
        self.table.setRowCount(len(point_list))

        for row, point in enumerate(point_list):
            for column, value in enumerate(
                [
                    str(point.index + 1),
                    format_power_w(point.requested_power_w, 5),
                    format_power_w(point.setpoint_w, 5),
                    format_power_w(point.expected_actual_power_w, 5),
                    str(point.filter_state),
                ]
            ):
                self.table.setItem(row, column, QTableWidgetItem(value))

        self.table.resizeColumnsToContents()
        if warning_list:
            self.warning_label.setText(f"{len(warning_list)} warning(s).")
            self.warning_label.setToolTip("\n".join(warning_list))
        else:
            self.warning_label.clear()
            self.warning_label.setToolTip("")

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self._apply_control_state()

    def set_instrument_availability(
        self,
        *,
        spectrometer_available: bool,
        power_meter_available: bool,
        lasers_available: bool,
    ) -> None:
        self._spectrometer_available = bool(
            spectrometer_available
        )
        self._power_meter_available = bool(
            power_meter_available
        )
        self._lasers_available = bool(
            lasers_available
        )

        self._apply_control_state()

    def _apply_control_state(self) -> None:
        idle = not self._running

        self.preview_button.setEnabled(
            idle and self._lasers_available
        )

        self.run_button.setEnabled(
            idle
            and self._lasers_available
            and self._spectrometer_available
        )

        self.calibration_button.setEnabled(
            idle
            and self._lasers_available
            and self._power_meter_available
        )

        self.load_calibration_button.setEnabled(idle)
        self.save_calibration_button.setEnabled(idle)
        self.abort_button.setEnabled(self._running)

        for widget in (
            self.basis_combo,
            self.spacing_combo,
            self.calibration_reads,
            self.start_power,
            self.stop_power,
            self.power_units,
            self.n_points,
            self.repeats_per_point,
            self.settling_time_s,
            self.enable_before_scan,
            self.disable_after_scan,
            self.autosave_scan_spectra,
            self.custom_values,
        ):
            widget.setEnabled(idle)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _set_combo_text(combo: QComboBox, value: str) -> None:
        index = combo.findText(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)

    def load_preferences(self, settings: QSettings) -> None:
        self._set_combo_data(
            self.basis_combo,
            get_str(settings, "scan/basis", self.scan_basis()),
        )
        self._set_combo_data(
            self.spacing_combo,
            get_str(settings, "scan/spacing", self.spacing()),
        )
        self._set_combo_text(
            self.power_units,
            get_str(settings, "scan/power_units", self.power_units.currentText()),
        )
        self.start_power.setValue(
            get_float(settings, "scan/start_power", self.start_power.value())
        )
        self.stop_power.setValue(
            get_float(settings, "scan/stop_power", self.stop_power.value())
        )
        self.n_points.setValue(get_int(settings, "scan/n_points", self.n_points.value()))
        self.repeats_per_point.setValue(
            get_int(settings, "scan/repeats_per_point", self.repeats_per_point.value())
        )
        self.calibration_reads.setValue(
            get_int(settings, "scan/calibration_reads", self.calibration_reads.value())
        )
        self.settling_time_s.setValue(
            get_float(settings, "scan/settling_time_s", self.settling_time_s.value())
        )
        self.enable_before_scan.setChecked(
            get_bool(settings, "scan/enable_before", self.enable_before_scan.isChecked())
        )
        self.disable_after_scan.setChecked(
            get_bool(settings, "scan/disable_after", self.disable_after_scan.isChecked())
        )
        self.autosave_scan_spectra.setChecked(
            get_bool(settings, "scan/autosave", self.autosave_scan_spectra.isChecked())
        )
        self.custom_values.setPlainText(
            get_str(settings, "scan/custom_values", self.custom_values.toPlainText())
        )
        self._on_spacing_changed()

    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("scan/basis", self.scan_basis())
        settings.setValue("scan/spacing", self.spacing())
        settings.setValue("scan/power_units", self.power_units.currentText())
        settings.setValue("scan/start_power", self.start_power.value())
        settings.setValue("scan/stop_power", self.stop_power.value())
        settings.setValue("scan/n_points", self.n_points.value())
        settings.setValue("scan/repeats_per_point", self.repeats_per_point.value())
        settings.setValue("scan/calibration_reads", self.calibration_reads.value())
        settings.setValue("scan/settling_time_s", self.settling_time_s.value())
        settings.setValue("scan/enable_before", self.enable_before_scan.isChecked())
        settings.setValue("scan/disable_after", self.disable_after_scan.isChecked())
        settings.setValue("scan/autosave", self.autosave_scan_spectra.isChecked())
        settings.setValue("scan/custom_values", self.custom_values.toPlainText())
