# panels/laser_panel.py

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal, Slot, QSettings
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.laser_models import LaserChannelInfo, LaserEmissionState
from core.preferences import get_bool, get_str


_POWER_FACTORS = {
    "W": 1.0,
    "mW": 1e-3,
    "uW": 1e-6,
    "nW": 1e-9,
}


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _sort_wavelength_key(laser: LaserChannelInfo) -> tuple[float, str, int]:
    wl = float(laser.wavelength_nm)

    if not math.isfinite(wl):
        wl = 1.0e99

    return wl, str(laser.port), int(laser.channel)


def _fmt_float(value: float, digits: int = 5) -> str:
    if not _finite(value):
        return "--"

    return f"{float(value):.{digits}g}"


def _fmt_wavelength_nm(wavelength_nm: float) -> str:
    if not _finite(wavelength_nm):
        return "--"

    return f"{float(wavelength_nm):.1f} nm"


def _fmt_power_w(power_w: float) -> str:
    if not _finite(power_w):
        return "--"

    p = float(power_w)
    ap = abs(p)

    if ap >= 1.0:
        return f"{p:.4g} W"
    if ap >= 1e-3:
        return f"{p * 1e3:.4g} mW"
    if ap >= 1e-6:
        return f"{p * 1e6:.4g} uW"
    if ap >= 1e-9:
        return f"{p * 1e9:.4g} nW"

    return f"{p:.3e} W"


def _enabled_bool(state: LaserEmissionState) -> bool:
    return state == LaserEmissionState.ON


def _wavelength_pastel_color(wavelength_nm: float) -> QColor:
    """
    Returns a subtle, low-alpha color associated with the laser wavelength.

    The exact color is only a visual cue, not a calibrated wavelength-to-RGB map.
    Alpha=55/255 is intentionally mild so the table remains readable.
    """

    if not _finite(wavelength_nm):
        return QColor(230, 230, 230, 35)

    wl = float(wavelength_nm)

    if wl < 420:
        return QColor(185, 160, 255, 55)   # violet
    elif wl < 460:
        return QColor(145, 190, 255, 55)   # blue
    elif wl < 500:
        return QColor(0, 250, 255, 64)     # cyan, close to your 488 nm example
    elif wl < 540:
        return QColor(140, 255, 210, 55)   # blue-green
    elif wl < 570:
        return QColor(170, 255, 145, 55)   # green
    elif wl < 590:
        return QColor(245, 255, 130, 55)   # yellow-green
    elif wl < 620:
        return QColor(255, 210, 125, 55)   # orange
    elif wl < 690:
        return QColor(255, 145, 145, 55)   # red
    elif wl < 780:
        return QColor(255, 130, 185, 50)   # deep red / near-IR cue

    return QColor(210, 210, 210, 40)


class LaserPanel(QWidget):
    refresh_requested = Signal()
    set_power_requested = Signal(str, int, float)
    set_enabled_requested = Signal(str, int, bool)
    disable_all_requested = Signal()
    set_cdrh_delay_requested = Signal(str, int, bool)

    COL_WAVELENGTH = 0
    COL_SETPOINT = 1
    COL_MIN = 2
    COL_MAX = 3
    COL_NOMINAL = 4
    COL_EMISSION = 5
    COL_CHANNEL = 6
    COL_BOX = 7
    COL_PORT = 8

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._lasers: list[LaserChannelInfo] = []
        self._laser_by_key: dict[tuple[str, int], LaserChannelInfo] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        top = QHBoxLayout()

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)

        self.disable_all_button = QPushButton("Disable All")
        self.disable_all_button.clicked.connect(self.disable_all_requested.emit)

        self.show_details_check = QCheckBox("Details")
        self.show_details_check.setChecked(False)
        self.show_details_check.toggled.connect(self._apply_detail_column_visibility)

        top.addWidget(self.refresh_button)
        top.addWidget(self.disable_all_button)
        top.addStretch(1)
        top.addWidget(self.show_details_check)

        layout.addLayout(top)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            [
                "λ",
                "Set",
                "Min",
                "Max",
                "Nom",
                "On",
                "Ch",
                "Box",
                "Port",
            ]
        )

        self.setMinimumWidth(260)

        self.table.setMinimumWidth(250)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)

        self.table.setColumnWidth(self.COL_WAVELENGTH, 62)
        self.table.setColumnWidth(self.COL_SETPOINT, 70)
        self.table.setColumnWidth(self.COL_MIN, 62)
        self.table.setColumnWidth(self.COL_MAX, 62)
        self.table.setColumnWidth(self.COL_NOMINAL, 62)
        self.table.setColumnWidth(self.COL_EMISSION, 48)

        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(False)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        layout.addWidget(self.table, stretch=1)

        power_row = QHBoxLayout()

        power_row.addWidget(QLabel("Set power"))

        self.power_spin = QDoubleSpinBox()
        self.power_spin.setRange(0.0, 1.0e9)
        self.power_spin.setDecimals(2)
        self.power_spin.setSingleStep(0.1)
        self.power_spin.setValue(1.00)
        self.power_spin.setMaximumWidth(90)
        self.power_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)

        self.power_units = QComboBox()
        self.power_units.addItems(["W", "mW", "μW", "nW"])
        self.power_units.setMaximumWidth(72)
        self.power_units.currentTextChanged.connect(self._on_power_units_changed)

        self.set_power_button = QPushButton("Set")
        self.set_power_button.setMaximumWidth(48)
        self.set_power_button.clicked.connect(self._on_set_power)

        power_row.addWidget(self.power_spin)
        power_row.addWidget(self.power_units)
        power_row.addWidget(self.set_power_button)

        layout.addLayout(power_row)

        self.selected_detail_label = QLabel("Selected: --")
        self.selected_detail_label.setWordWrap(True)
        layout.addWidget(self.selected_detail_label)
        
        self.cdrh_delay_check = QCheckBox("CDRH delay")
        self.cdrh_delay_check.setToolTip(
            "Enable/disable the laser emission delay for the selected laser."
        )
        self.cdrh_delay_check.toggled.connect(self._on_cdrh_delay_toggled)

        layout.addWidget(self.cdrh_delay_check)

        self._apply_detail_column_visibility(False)

    @Slot(object)
    def set_lasers(self, lasers: object) -> None:
        print("LaserPanel.set_lasers received", len(list(lasers)), "lasers")
        for laser in lasers:
            print(
                "  ",
                laser.port,
                laser.channel,
                laser.wavelength_nm,
                laser.setpoint_w,
                laser.box_id,
            )

        self._lasers = sorted(list(lasers), key=_sort_wavelength_key)
        self._laser_by_key = {
            (str(laser.port), int(laser.channel)): laser
            for laser in self._lasers
        }

        self.table.clearContents()
        self.table.setRowCount(len(self._lasers))

        if not self._lasers:
            self.selected_detail_label.setText("Selected: no lasers found")
            return

        for row, laser in enumerate(self._lasers):
            self._populate_row(row, laser)

        self._apply_detail_column_visibility(self.show_details_check.isChecked())
        self.table.resizeColumnsToContents()
        self._on_selection_changed()

    def _populate_row(self, row: int, laser: LaserChannelInfo) -> None:
        bg = _wavelength_pastel_color(float(laser.wavelength_nm))
        brush = QBrush(bg)

        key = {
            "port": str(laser.port),
            "channel": int(laser.channel),
        }

        values = [
            _fmt_wavelength_nm(laser.wavelength_nm),
            _fmt_power_w(laser.setpoint_w),
            _fmt_power_w(laser.min_setpoint_w),
            _fmt_power_w(laser.max_setpoint_w),
            _fmt_power_w(laser.nominal_power_w),
            "",  # cell widget goes here
            str(laser.channel),
            str(laser.box_id),
            str(laser.port),
        ]

        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setBackground(brush)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            tooltip = (
                f"Port: {laser.port}\n"
                f"Box: {laser.box_id}\n"
                f"Channel: {laser.channel}\n"
                f"IDN: {laser.idn}\n"
                f"Wavelength: {_fmt_wavelength_nm(laser.wavelength_nm)}\n"
                f"Setpoint: {_fmt_power_w(laser.setpoint_w)}\n"
                f"Range: {_fmt_power_w(laser.min_setpoint_w)} to {_fmt_power_w(laser.max_setpoint_w)}"
            )
            item.setToolTip(tooltip)

            if col == self.COL_WAVELENGTH:
                item.setData(Qt.ItemDataRole.UserRole, key)

            self.table.setItem(row, col, item)

        enabled = _enabled_bool(laser.enabled)

        button = QPushButton("ON" if enabled else "OFF")
        button.setCheckable(True)
        button.setChecked(enabled)
        button.setMinimumWidth(46)
        button.setMaximumWidth(58)

        if enabled:
            button.setToolTip("Click to disable this laser.")
        else:
            button.setToolTip("Click to enable this laser.")

        button.toggled.connect(
            lambda checked, b=button: b.setText("ON" if checked else "OFF")
        )
        button.toggled.connect(
            lambda checked, port=str(laser.port), channel=int(laser.channel): (
                self.set_enabled_requested.emit(port, channel, bool(checked))
            )
        )

        self.table.setCellWidget(row, self.COL_EMISSION, button)

    def _apply_detail_column_visibility(self, show: bool) -> None:
        show = bool(show)

        for col in [self.COL_CHANNEL, self.COL_BOX, self.COL_PORT]:
            self.table.setColumnHidden(col, not show)

    def selected_laser_key(self) -> tuple[str, int] | None:
        row = self.table.currentRow()

        if row < 0:
            return None

        item = self.table.item(row, self.COL_WAVELENGTH)

        if item is None:
            return None

        data = item.data(Qt.ItemDataRole.UserRole)

        if not data:
            return None

        return str(data["port"]), int(data["channel"])

    def selected_laser(self) -> LaserChannelInfo | None:
        key = self.selected_laser_key()

        if key is None:
            return None

        return self._laser_by_key.get(key)

    def laser_by_key(self, port: str, channel: int) -> LaserChannelInfo | None:
        return self._laser_by_key.get((str(port), int(channel)))

    def _selected_or_warn(self) -> LaserChannelInfo | None:
        laser = self.selected_laser()

        if laser is None:
            QMessageBox.information(self, "No laser selected", "Select a laser channel first.")
            return None

        return laser

    def _current_power_w(self) -> float:
        units = self.power_units.currentText()
        factor = _POWER_FACTORS[units]
        return float(self.power_spin.value()) * factor

    def _on_set_power(self) -> None:
        laser = self._selected_or_warn()

        if laser is None:
            return

        self.set_power_requested.emit(
            str(laser.port),
            int(laser.channel),
            self._current_power_w(),
        )

    def _on_selection_changed(self) -> None:
        laser = self.selected_laser()

        if laser is None:
            self.selected_detail_label.setText("Selected: --")
            self.cdrh_delay_check.setEnabled(False)
            return

        self.selected_detail_label.setText(
            "Selected: "
            f"{_fmt_wavelength_nm(laser.wavelength_nm)}, "
            f"{_fmt_power_w(laser.setpoint_w)} set, "
            f"{laser.port} ch{laser.channel}"
        )

        self._load_selected_setpoint_into_spinbox(laser)

        cdrh_value = getattr(laser, "cdrh_delay_enabled", None)

        self.cdrh_delay_check.blockSignals(True)

        if cdrh_value is None:
            self.cdrh_delay_check.setEnabled(False)
            self.cdrh_delay_check.setChecked(False)
        else:
            self.cdrh_delay_check.setEnabled(True)
            self.cdrh_delay_check.setChecked(bool(cdrh_value))

        self.cdrh_delay_check.blockSignals(False)

    def _on_power_units_changed(self) -> None:
        laser = self.selected_laser()

        if laser is not None:
            self._load_selected_setpoint_into_spinbox(laser)

    def _load_selected_setpoint_into_spinbox(self, laser: LaserChannelInfo) -> None:
        if not _finite(laser.setpoint_w):
            return

        units = self.power_units.currentText()
        factor = _POWER_FACTORS[units]

        if factor <= 0:
            return

        self.power_spin.blockSignals(True)
        self.power_spin.setValue(float(laser.setpoint_w) / factor)
        self.power_spin.blockSignals(False)

    def _on_cdrh_delay_toggled(self, enabled: bool) -> None:
        laser = self.selected_laser()

        if laser is None:
            return

        self.set_cdrh_delay_requested.emit(
            str(laser.port),
            int(laser.channel),
            bool(enabled),
        )

    def load_preferences(self, settings: QSettings) -> None:
        self.show_details_check.setChecked(
            get_bool(settings, "laser/show_details", self.show_details_check.isChecked())
        )

        units = get_str(settings, "laser/power_units", self.power_units.currentText())
        index = self.power_units.findText(units)

        if index >= 0:
            self.power_units.setCurrentIndex(index)


    def save_preferences(self, settings: QSettings) -> None:
        settings.setValue("laser/show_details", self.show_details_check.isChecked())
        settings.setValue("laser/power_units", self.power_units.currentText())
