from __future__ import annotations

import math
from dataclasses import replace

from PySide6.QtCore import QSettings, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QColor
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

from core.colors import wavelength_to_rgb
from core.laser_models import LaserChannelInfo, LaserEmissionState
from core.preferences import get_bool, get_str
from core.units import format_power_w


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _sort_wavelength_key(laser: LaserChannelInfo) -> tuple[float, str, int]:
    wavelength = float(laser.wavelength_nm)
    return (
        wavelength if math.isfinite(wavelength) else float("inf"),
        str(laser.port),
        int(laser.channel),
    )


def _format_wavelength(wavelength_nm: float) -> str:
    return f"{float(wavelength_nm):.1f} nm" if _finite(wavelength_nm) else "--"


def _row_color(wavelength_nm: float) -> QColor:
    red, green, blue = wavelength_to_rgb(float(wavelength_nm))
    return QColor(red, green, blue, 55)


class LaserPanel(QWidget):
    """Compact table and controls for available OBIS laser channels."""

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
        layout.addLayout(self._build_toolbar())

        self.table = self._build_table()
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(self._build_power_controls())

        self.selected_detail_label = QLabel("Selected: --")
        self.selected_detail_label.setWordWrap(True)
        layout.addWidget(self.selected_detail_label)

        self.cdrh_delay_check = QCheckBox("CDRH delay")
        self.cdrh_delay_check.setToolTip(
            "Enable or disable the emission delay for the selected laser."
        )
        self.cdrh_delay_check.toggled.connect(self._on_cdrh_delay_toggled)
        layout.addWidget(self.cdrh_delay_check)

        self._apply_detail_column_visibility(False)

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda _checked=False: self.refresh_requested.emit())
        self.disable_all_button = QPushButton("Disable All")
        self.disable_all_button.clicked.connect(
            lambda _checked=False: self.disable_all_requested.emit()
        )
        self.show_details_check = QCheckBox("Details")
        self.show_details_check.toggled.connect(self._apply_detail_column_visibility)
        row.addWidget(self.refresh_button)
        row.addWidget(self.disable_all_button)
        row.addStretch(1)
        row.addWidget(self.show_details_check)
        return row

    def _build_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(9)
        table.setHorizontalHeaderLabels(
            ["λ", "Set", "Min", "Max", "Nom", "On", "Ch", "Box", "Port"]
        )
        table.setMinimumWidth(250)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(22)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(False)
        table.setWordWrap(False)
        table.itemSelectionChanged.connect(self._on_selection_changed)
        return table

    def _build_power_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Set power"))

        self.power_spin = QDoubleSpinBox()
        self.power_spin.setRange(0.0, 1.0e9)
        self.power_spin.setDecimals(2)
        self.power_spin.setSingleStep(0.1)
        self.power_spin.setValue(1.0)
        self.power_spin.setMaximumWidth(90)
        self.power_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)

        self.power_units = QComboBox()
        self.power_units.addItem("W", 1.0)
        self.power_units.addItem("mW", 1e-3)
        self.power_units.addItem("μW", 1e-6)
        self.power_units.addItem("nW", 1e-9)
        self.power_units.setMaximumWidth(72)
        self.power_units.currentIndexChanged.connect(
            lambda _index: self._load_selected_setpoint_into_spinbox()
        )

        self.set_power_button = QPushButton("Set")
        self.set_power_button.setMaximumWidth(48)
        self.set_power_button.clicked.connect(self._on_set_power)

        row.addWidget(self.power_spin)
        row.addWidget(self.power_units)
        row.addWidget(self.set_power_button)
        return row

    @Slot(object)
    def set_lasers(self, lasers: object) -> None:
        selected_key = self.selected_laser_key()
        self._lasers = sorted(list(lasers), key=_sort_wavelength_key)
        self._laser_by_key = {
            (str(laser.port), int(laser.channel)): laser for laser in self._lasers
        }

        self.table.clearContents()
        self.table.setRowCount(len(self._lasers))
        for row, laser in enumerate(self._lasers):
            self._populate_row(row, laser)

        self._apply_detail_column_visibility(self.show_details_check.isChecked())
        self.table.resizeColumnsToContents()

        if selected_key is not None:
            self._select_key(*selected_key)
        elif self._lasers:
            self.table.selectRow(0)
        else:
            self.selected_detail_label.setText("Selected: no lasers found")
            self.cdrh_delay_check.setEnabled(False)

    def _populate_row(self, row: int, laser: LaserChannelInfo) -> None:
        brush = QBrush(_row_color(laser.wavelength_nm))
        key = (str(laser.port), int(laser.channel))
        values = [
            _format_wavelength(laser.wavelength_nm),
            format_power_w(laser.setpoint_w),
            format_power_w(laser.min_setpoint_w),
            format_power_w(laser.max_setpoint_w),
            format_power_w(laser.nominal_power_w),
            "",
            str(laser.channel),
            str(laser.box_id),
            str(laser.port),
        ]

        tooltip = (
            f"Port: {laser.port}\n"
            f"Box: {laser.box_id}\n"
            f"Channel: {laser.channel}\n"
            f"IDN: {laser.idn}\n"
            f"Wavelength: {_format_wavelength(laser.wavelength_nm)}\n"
            f"Setpoint: {format_power_w(laser.setpoint_w)}\n"
            f"Range: {format_power_w(laser.min_setpoint_w)} to "
            f"{format_power_w(laser.max_setpoint_w)}"
        )

        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setBackground(brush)
            item.setToolTip(tooltip)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if column == self.COL_WAVELENGTH:
                item.setData(Qt.ItemDataRole.UserRole, key)
            self.table.setItem(row, column, item)

        enabled = laser.enabled == LaserEmissionState.ON
        button = QPushButton("ON" if enabled else "OFF")
        button.setCheckable(True)
        button.setChecked(enabled)
        button.setMinimumWidth(46)
        button.setMaximumWidth(58)
        button.setToolTip(
            "Click to disable this laser." if enabled else "Click to enable this laser."
        )
        button.toggled.connect(
            lambda checked, b=button: b.setText("ON" if checked else "OFF")
        )
        button.toggled.connect(
            lambda checked, port=key[0], channel=key[1]: self.set_enabled_requested.emit(
                port,
                channel,
                bool(checked),
            )
        )
        self.table.setCellWidget(row, self.COL_EMISSION, button)

    def _row_for_key(self, port: str, channel: int) -> int | None:
        target = (str(port), int(channel))
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_WAVELENGTH)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == target:
                return row
        return None

    def _select_key(self, port: str, channel: int) -> None:
        row = self._row_for_key(port, channel)
        if row is not None:
            self.table.selectRow(row)

    def update_setpoint(self, port: str, channel: int, power_w: float) -> None:
        key = (str(port), int(channel))
        laser = self._laser_by_key.get(key)
        if laser is None:
            return
        updated = replace(laser, setpoint_w=float(power_w))
        self._laser_by_key[key] = updated
        self._lasers = [updated if item == laser else item for item in self._lasers]
        row = self._row_for_key(*key)
        if row is not None:
            self.table.item(row, self.COL_SETPOINT).setText(format_power_w(power_w))
        self._on_selection_changed()

    def _set_enabled_button(self, port: str, channel: int, enabled: bool) -> None:
        row = self._row_for_key(port, channel)
        if row is None:
            return
        button = self.table.cellWidget(row, self.COL_EMISSION)
        if not isinstance(button, QPushButton):
            return

        button.blockSignals(True)
        button.setChecked(bool(enabled))
        button.setText("ON" if enabled else "OFF")
        button.setToolTip(
            "Click to disable this laser."
            if enabled
            else "Click to enable this laser."
        )
        button.blockSignals(False)

    def update_enabled(self, port: str, channel: int, enabled: bool) -> None:
        key = (str(port), int(channel))
        laser = self._laser_by_key.get(key)
        if laser is None:
            return
        state = LaserEmissionState.ON if enabled else LaserEmissionState.OFF
        updated = replace(laser, enabled=state)
        self._laser_by_key[key] = updated
        self._lasers = [updated if item == laser else item for item in self._lasers]
        self._set_enabled_button(*key, enabled=bool(enabled))

    @Slot(str, int, str)
    def restore_enabled_after_failure(
        self,
        port: str,
        channel: int,
        _message: str,
    ) -> None:
        laser = self._laser_by_key.get((str(port), int(channel)))
        if laser is not None:
            self._set_enabled_button(
                str(port),
                int(channel),
                enabled=laser.enabled == LaserEmissionState.ON,
            )

    def update_cdrh(self, port: str, channel: int, enabled: bool) -> None:
        key = (str(port), int(channel))
        laser = self._laser_by_key.get(key)
        if laser is None:
            return
        updated = replace(laser, cdrh_delay_enabled=bool(enabled))
        self._laser_by_key[key] = updated
        self._lasers = [updated if item == laser else item for item in self._lasers]
        self._on_selection_changed()

    @Slot(str, int, str)
    def restore_cdrh_after_failure(
        self,
        port: str,
        channel: int,
        _message: str,
    ) -> None:
        selected = self.selected_laser_key()
        if selected == (str(port), int(channel)):
            self._on_selection_changed()

    def _apply_detail_column_visibility(self, show: bool) -> None:
        for column in (self.COL_CHANNEL, self.COL_BOX, self.COL_PORT):
            self.table.setColumnHidden(column, not bool(show))

    def selected_laser_key(self) -> tuple[str, int] | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, self.COL_WAVELENGTH)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 2:
            return None
        return str(data[0]), int(data[1])

    def selected_laser(self) -> LaserChannelInfo | None:
        key = self.selected_laser_key()
        return self._laser_by_key.get(key) if key is not None else None

    def laser_by_key(self, port: str, channel: int) -> LaserChannelInfo | None:
        return self._laser_by_key.get((str(port), int(channel)))

    def _selected_or_warn(self) -> LaserChannelInfo | None:
        laser = self.selected_laser()
        if laser is None:
            QMessageBox.information(self, "No laser selected", "Select a laser channel first.")
        return laser

    def _current_power_w(self) -> float:
        return float(self.power_spin.value()) * float(self.power_units.currentData())

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
            f"{_format_wavelength(laser.wavelength_nm)}, "
            f"{format_power_w(laser.setpoint_w)} set, "
            f"{laser.port} ch{laser.channel}"
        )
        self._load_selected_setpoint_into_spinbox()

        self.cdrh_delay_check.blockSignals(True)
        if laser.cdrh_delay_enabled is None:
            self.cdrh_delay_check.setEnabled(False)
            self.cdrh_delay_check.setChecked(False)
        else:
            self.cdrh_delay_check.setEnabled(True)
            self.cdrh_delay_check.setChecked(bool(laser.cdrh_delay_enabled))
        self.cdrh_delay_check.blockSignals(False)

    def _load_selected_setpoint_into_spinbox(self) -> None:
        laser = self.selected_laser()
        if laser is None or not _finite(laser.setpoint_w):
            return
        factor = float(self.power_units.currentData())
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
