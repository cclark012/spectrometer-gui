from __future__ import annotations

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core.records import InstrumentConnectionState


class InstrumentConnectionsDialog(QDialog):
    connect_requested = Signal(str, str, str)
    disconnect_requested = Signal(str)
    reconnect_all_requested = Signal()

    LABELS = {
        "spectrometer": "Spectrometer",
        "power_meter": "Power meter",
        "lasers": "Laser boxes",
    }

    SOURCES = {
        "spectrometer": (
            ("Auto-detect real (QEPro → Andor)", "real", "auto"),
            ("QEPro (real)", "real", "qepro"),
            ("Andor iDus + Kymera (real)", "real", "andor"),
            ("Spectrometer emulator", "emulated", "qepro"),
            ("Disconnected", "disconnected", "qepro"),
        ),
        "power_meter": (
            ("Newport 2936-R (real)", "real", "newport_2936r"),
            ("Power-meter emulator", "emulated", "newport_2936r"),
            ("Disconnected", "disconnected", "newport_2936r"),
        ),
        "lasers": (
            ("OBIS boxes (real)", "real", "obis"),
            ("OBIS real, then emulator", "auto", "obis"),
            ("OBIS emulators", "emulated", "obis"),
            ("Disconnected", "disconnected", "obis"),
        ),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setWindowTitle(
            "Instrument Connections"
        )
        self.resize(760, 260)

        self._states: dict[
            str,
            InstrumentConnectionState,
        ] = {}

        self._status_labels: dict[str, QLabel] = {}
        self._buttons: dict[str, QPushButton] = {}
        self._selectors: dict[str, QComboBox] = {}

        layout = QVBoxLayout(self)
        grid = QGridLayout()

        grid.addWidget(QLabel("Instrument"), 0, 0)
        grid.addWidget(QLabel("Source"), 0, 1)
        grid.addWidget(QLabel("Status"), 0, 2)
        grid.addWidget(QLabel("Action"), 0, 3)

        for row, key in enumerate(
            self.LABELS,
            start=1,
        ):
            name = QLabel(self.LABELS[key])
            selector = QComboBox()
            for label, mode, backend in self.SOURCES[key]:
                selector.addItem(label, (mode, backend))
            status = QLabel("Disconnected")
            button = QPushButton("Connect")

            button.clicked.connect(
                lambda _checked=False, selected=key: self._on_action(selected)
            )

            self._status_labels[key] = status
            self._buttons[key] = button
            self._selectors[key] = selector

            selector.currentIndexChanged.connect(
                lambda _index, selected=key: self._refresh_action(selected)
            )

            grid.addWidget(name, row, 0)
            grid.addWidget(selector, row, 1)
            grid.addWidget(status, row, 2)
            grid.addWidget(button, row, 3)

        layout.addLayout(grid)

        reconnect = QPushButton("Reconnect All")
        reconnect.clicked.connect(
            self.reconnect_all_requested.emit
        )
        layout.addWidget(reconnect)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close
        )
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    @Slot(str)
    def _on_action(self, key: str) -> None:
        state = self._states.get(key)

        if state is not None and state.connected:
            self.disconnect_requested.emit(key)
        else:
            mode, backend = self.selection(key)
            if mode != "disconnected":
                self.connect_requested.emit(key, mode, backend)

    def selection(self, key: str) -> tuple[str, str]:
        selector = self._selectors[str(key)]
        value = selector.currentData()
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            return "disconnected", ""
        return str(value[0]), str(value[1])

    def set_selection(self, key: str, *, mode: str, backend: str = "") -> None:
        selector = self._selectors.get(str(key))
        if selector is None:
            return
        target_mode = str(mode)
        target_backend = str(backend)
        best_index = -1
        for index in range(selector.count()):
            value = selector.itemData(index)
            if not isinstance(value, (tuple, list)) or len(value) != 2:
                continue
            item_mode, item_backend = str(value[0]), str(value[1])
            if item_mode == target_mode and (
                str(key) != "spectrometer"
                or target_mode != "real"
                or item_backend == target_backend
            ):
                best_index = index
                break
        if best_index >= 0:
            selector.blockSignals(True)
            selector.setCurrentIndex(best_index)
            selector.blockSignals(False)
        self._refresh_action(str(key))

    def _refresh_action(self, key: str) -> None:
        state = self._states.get(str(key))
        connected = bool(state and state.connected)
        selector = self._selectors[str(key)]
        button = self._buttons[str(key)]
        selector.setEnabled(not connected)
        if connected:
            button.setEnabled(True)
            button.setText("Disconnect")
            return
        mode, _backend = self.selection(str(key))
        button.setEnabled(mode != "disconnected")
        button.setText("Connect")

    @Slot(object)
    def set_state(
        self,
        state: InstrumentConnectionState,
    ) -> None:
        self._states[state.key] = state

        label = self._status_labels.get(
            state.key
        )
        button = self._buttons.get(state.key)

        if label is None or button is None:
            return

        if state.connected:
            mode = (
                "emulated"
                if state.emulated
                else "real"
            )

            label.setText(
                f"Connected ({mode})"
            )
        else:
            label.setText("Disconnected")

        self._refresh_action(state.key)

        details = (
            state.error
            or state.description
            or ""
        )

        label.setToolTip(details)
