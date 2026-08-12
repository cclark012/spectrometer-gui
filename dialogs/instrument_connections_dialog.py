from __future__ import annotations

from functools import partial

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core.records import InstrumentConnectionState


class InstrumentConnectionsDialog(QDialog):
    connect_requested = Signal(str)
    disconnect_requested = Signal(str)
    reconnect_all_requested = Signal()

    LABELS = {
        "spectrometer": "Spectrometer",
        "power_meter": "Power meter",
        "lasers": "Laser boxes",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setWindowTitle(
            "Instrument Connections"
        )
        self.resize(520, 240)

        self._states: dict[
            str,
            InstrumentConnectionState,
        ] = {}

        self._status_labels: dict[str, QLabel] = {}
        self._buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        grid = QGridLayout()

        grid.addWidget(QLabel("Instrument"), 0, 0)
        grid.addWidget(QLabel("Status"), 0, 1)
        grid.addWidget(QLabel("Action"), 0, 2)

        for row, key in enumerate(
            self.LABELS,
            start=1,
        ):
            name = QLabel(self.LABELS[key])
            status = QLabel("Disconnected")
            button = QPushButton("Connect")

            button.clicked.connect(
                partial(self._on_action, key)
            )

            self._status_labels[key] = status
            self._buttons[key] = button

            grid.addWidget(name, row, 0)
            grid.addWidget(status, row, 1)
            grid.addWidget(button, row, 2)

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
            self.connect_requested.emit(key)

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
            button.setText("Disconnect")
        else:
            label.setText("Disconnected")
            button.setText("Connect")

        details = (
            state.error
            or state.description
            or ""
        )

        label.setToolTip(details)
