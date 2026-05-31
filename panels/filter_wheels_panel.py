# panels/filter_wheel_panel.py

from __future__ import annotations

import json

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.filter_models import FilterPosition, FilterWheel
from planning.filter_planning import enumerate_filter_states


DEFAULT_WHEELS = [
    {
        "name": "Wheel 1",
        "positions": [
            {"label": "open", "od": 0.0},
            {"label": "OD0.5", "od": 0.5},
            {"label": "OD1", "od": 1.0},
            {"label": "OD2", "od": 2.0},
        ],
    },
    {
        "name": "Wheel 2",
        "positions": [
            {"label": "open", "od": 0.0},
            {"label": "OD0.5", "od": 0.5},
            {"label": "OD1", "od": 1.0},
            {"label": "OD2", "od": 2.0},
        ],
    },
]


class FilterWheelPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._wheel_dicts = json.loads(json.dumps(DEFAULT_WHEELS))

        layout = QVBoxLayout(self)

        self.enable_planner_check = QCheckBox("Use ND filter planner")
        self.enable_planner_check.setChecked(False)
        self.enable_planner_check.toggled.connect(self._on_enable_planner_toggled)
        layout.addWidget(self.enable_planner_check)

        top = QHBoxLayout()

        top.addWidget(QLabel("Wheel"))

        self.wheel_combo = QComboBox()
        self.wheel_combo.currentIndexChanged.connect(self._on_wheel_index_changed)
        top.addWidget(self.wheel_combo, stretch=1)

        self.add_wheel_button = QPushButton("+ Wheel")
        self.add_wheel_button.clicked.connect(self.add_wheel)
        top.addWidget(self.add_wheel_button)

        self.remove_wheel_button = QPushButton("Remove")
        self.remove_wheel_button.clicked.connect(self.remove_selected_wheel)
        top.addWidget(self.remove_wheel_button)

        layout.addLayout(top)

        form = QFormLayout()

        self.wheel_name_edit = QLineEdit()
        self.wheel_name_edit.editingFinished.connect(self._store_selected_wheel_name)
        form.addRow("Name", self.wheel_name_edit)

        layout.addLayout(form)

        self.position_table = QTableWidget()
        self.position_table.setColumnCount(2)
        self.position_table.setHorizontalHeaderLabels(["Position", "OD"])
        self.position_table.cellChanged.connect(self._on_position_table_cell_changed)
        layout.addWidget(self.position_table, stretch=1)

        row_buttons = QHBoxLayout()

        self.add_position_button = QPushButton("+ Position")
        self.add_position_button.clicked.connect(self.add_position)
        row_buttons.addWidget(self.add_position_button)

        self.remove_position_button = QPushButton("Remove Position")
        self.remove_position_button.clicked.connect(self.remove_selected_position)
        row_buttons.addWidget(self.remove_position_button)

        layout.addLayout(row_buttons)

        self.states_table = QTableWidget()
        self.states_table.setColumnCount(3)
        self.states_table.setHorizontalHeaderLabels(["State", "OD", "Transmission"])
        self.states_table.setMaximumHeight(160)
        layout.addWidget(self.states_table)

        self._reload_wheel_combo()
        self._load_selected_wheel()
        self._update_states_table()

    @Slot(bool)
    def _on_enable_planner_toggled(self, checked: bool) -> None:
        self.changed.emit()

    @Slot(int)
    def _on_wheel_index_changed(self, index: int) -> None:
        self._load_selected_wheel()

    @Slot(int, int)
    def _on_position_table_cell_changed(self, row: int, column: int) -> None:
        self._store_position_table()

    def planner_enabled(self) -> bool:
        return bool(self.enable_planner_check.isChecked())

    def filter_wheels(self) -> list[FilterWheel]:
        wheels: list[FilterWheel] = []

        for wheel in self._wheel_dicts:
            name = str(wheel.get("name", "")).strip()

            if not name:
                continue

            positions = []

            for pos in wheel.get("positions", []):
                label = str(pos.get("label", "")).strip()

                if not label:
                    continue

                try:
                    od = float(pos.get("od", 0.0))
                except Exception:
                    od = 0.0

                positions.append(FilterPosition(label=label, optical_density=od))

            if positions:
                wheels.append(FilterWheel(name=name, positions=tuple(positions)))

        return wheels

    def serialize(self) -> str:
        return json.dumps(
            {
                "planner_enabled": self.planner_enabled(),
                "wheels": self._wheel_dicts,
            },
            indent=2,
        )

    def deserialize(self, text: str) -> None:
        data = json.loads(text)

        wheels = data.get("wheels", DEFAULT_WHEELS)

        if not isinstance(wheels, list) or not wheels:
            wheels = DEFAULT_WHEELS

        self._wheel_dicts = wheels
        self.enable_planner_check.setChecked(bool(data.get("planner_enabled", False)))

        self._reload_wheel_combo()
        self._load_selected_wheel()
        self._update_states_table()
        self.changed.emit()

    def _reload_wheel_combo(self) -> None:
        current = self.wheel_combo.currentIndex()

        self.wheel_combo.blockSignals(True)
        self.wheel_combo.clear()

        for wheel in self._wheel_dicts:
            self.wheel_combo.addItem(str(wheel.get("name", "Wheel")))

        if self.wheel_combo.count() > 0:
            self.wheel_combo.setCurrentIndex(min(max(current, 0), self.wheel_combo.count() - 1))

        self.wheel_combo.blockSignals(False)

    def _selected_wheel_index(self) -> int:
        return int(self.wheel_combo.currentIndex())

    def _selected_wheel_dict(self) -> dict | None:
        idx = self._selected_wheel_index()

        if idx < 0 or idx >= len(self._wheel_dicts):
            return None

        return self._wheel_dicts[idx]

    def _load_selected_wheel(self) -> None:
        wheel = self._selected_wheel_dict()

        if wheel is None:
            return

        self.wheel_name_edit.blockSignals(True)
        self.wheel_name_edit.setText(str(wheel.get("name", "")))
        self.wheel_name_edit.blockSignals(False)

        positions = list(wheel.get("positions", []))

        self.position_table.blockSignals(True)
        self.position_table.setRowCount(len(positions))

        for row, pos in enumerate(positions):
            self.position_table.setItem(row, 0, QTableWidgetItem(str(pos.get("label", ""))))
            self.position_table.setItem(row, 1, QTableWidgetItem(f"{float(pos.get('od', 0.0)):.6g}"))

        self.position_table.blockSignals(False)
        self.position_table.resizeColumnsToContents()

    def _store_selected_wheel_name(self) -> None:
        wheel = self._selected_wheel_dict()

        if wheel is None:
            return

        name = self.wheel_name_edit.text().strip() or f"Wheel {self._selected_wheel_index() + 1}"
        wheel["name"] = name

        self._reload_wheel_combo()
        self._update_states_table()
        self.changed.emit()

    def _store_position_table(self) -> None:
        wheel = self._selected_wheel_dict()

        if wheel is None:
            return

        positions = []

        for row in range(self.position_table.rowCount()):
            label_item = self.position_table.item(row, 0)
            od_item = self.position_table.item(row, 1)

            label = label_item.text().strip() if label_item else ""

            if not label:
                continue

            try:
                od = float(od_item.text()) if od_item else 0.0
            except Exception:
                od = 0.0

            positions.append({"label": label, "od": od})

        wheel["positions"] = positions
        self._update_states_table()
        self.changed.emit()

    def add_wheel(self) -> None:
        self._wheel_dicts.append(
            {
                "name": f"Wheel {len(self._wheel_dicts) + 1}",
                "positions": [{"label": "open", "od": 0.0}],
            }
        )
        self._reload_wheel_combo()
        self.wheel_combo.setCurrentIndex(len(self._wheel_dicts) - 1)
        self._load_selected_wheel()
        self._update_states_table()
        self.changed.emit()

    def remove_selected_wheel(self) -> None:
        idx = self._selected_wheel_index()

        if idx < 0 or idx >= len(self._wheel_dicts):
            return

        if len(self._wheel_dicts) <= 1:
            QMessageBox.information(self, "Cannot remove wheel", "At least one wheel is required.")
            return

        del self._wheel_dicts[idx]
        self._reload_wheel_combo()
        self._load_selected_wheel()
        self._update_states_table()
        self.changed.emit()

    def add_position(self) -> None:
        wheel = self._selected_wheel_dict()

        if wheel is None:
            return

        wheel.setdefault("positions", []).append({"label": "new", "od": 0.0})
        self._load_selected_wheel()
        self._update_states_table()
        self.changed.emit()

    def remove_selected_position(self) -> None:
        wheel = self._selected_wheel_dict()

        if wheel is None:
            return

        row = self.position_table.currentRow()

        if row < 0:
            return

        positions = list(wheel.get("positions", []))

        if len(positions) <= 1:
            QMessageBox.information(self, "Cannot remove position", "At least one position is required.")
            return

        if row < len(positions):
            del positions[row]

        wheel["positions"] = positions
        self._load_selected_wheel()
        self._update_states_table()
        self.changed.emit()

    def _update_states_table(self) -> None:
        try:
            states = enumerate_filter_states(self.filter_wheels())
        except Exception:
            states = []

        self.states_table.setRowCount(len(states))

        for row, state in enumerate(states):
            values = [
                state.label,
                f"{state.optical_density:.6g}",
                f"{state.transmission:.6e}",
            ]

            for col, value in enumerate(values):
                self.states_table.setItem(row, col, QTableWidgetItem(value))

        self.states_table.resizeColumnsToContents()
