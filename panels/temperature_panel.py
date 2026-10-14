import csv
import time
from collections import deque
from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QPushButton, QLabel, QFileDialog, QMessageBox
import pyqtgraph as pg
from io_utils.atomic import atomic_text_writer

class TemperaturePanel(QWidget):
    connect_requested = Signal(str, str)
    disconnect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = deque(maxlen=20000)
        self.origin = time.perf_counter()
        root = QVBoxLayout(self)
        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem('USB / COM', 'real')
        self.mode.addItem('Emulator', 'emulated')
        self.port = QLineEdit()
        self.port.setPlaceholderText('COM port, e.g. COM5')
        self.connect_button = QPushButton('Connect Lake Shore 335')
        self.connect_button.clicked.connect(lambda: self.connect_requested.emit(self.mode.currentData(), self.port.text().strip()))
        self.disconnect_button = QPushButton('Disconnect')
        self.disconnect_button.clicked.connect(lambda _checked=False: self.disconnect_requested.emit())
        for w in (self.mode, self.port, self.connect_button, self.disconnect_button):
            row.addWidget(w)
        root.addLayout(row)
        self.status = QLabel('Disconnected — read-only temperature monitoring')
        root.addWidget(self.status)
        self.readout = QLabel('A: --  B: --')
        root.addWidget(self.readout)
        self.plot = pg.PlotWidget()
        self.plot.addLegend()
        self.plot.setLabel('left', 'Temperature (K)')
        self.plot.setLabel('bottom', 'Elapsed time (s)')
        self.curves = {c: self.plot.plot(name=c, pen=pg.mkPen(color, width=1.5)) for c, color in [('lakeshore_A', '#e69f00'), ('lakeshore_B', '#56b4e9')]}
        root.addWidget(self.plot)
        save = QPushButton('Export temperature history')
        save.clicked.connect(self.save)
        root.addWidget(save)
        note = QLabel('Latest 20000 channel readings retained (about 2.8 hours at 1 Hz). Export before older readings roll out. No heater or setpoint commands are sent.')
        note.setWordWrap(True)
        root.addWidget(note)
        self.latest = {}
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_age)
        self.timer.start()

    def set_state(self, connected, description):
        self.status.setText(description)
        if not connected:
            self.latest = {}
            self.refresh_age()

    def add_readings(self, readings):
        for reading in readings:
            self.history.append(reading)
            self.latest[reading.source] = reading
        for source, curve in self.curves.items():
            points = [r for r in self.history if r.source == source]
            curve.setData([r.sampled_s - self.origin for r in points], [r.value if r.status == 0 else float('nan') for r in points])
        self.refresh_age()

    def refresh_age(self):
        now = time.perf_counter()
        self.readout.setText(' | '.join((f'{key[-1]}: {r.value:g} K, age {now - r.sampled_s:.1f} s, status {r.status}' for key, r in self.latest.items())) or 'A: --  B: --')

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export temperature history', 'temperature.csv', 'CSV files (*.csv)')
        if not path:
            return
        try:
            with atomic_text_writer(path) as stream:
                writer = csv.writer(stream)
                writer.writerow(['timestamp_utc', 'elapsed_s', 'source', 'temperature', 'units', 'status', 'identity'])
                for r in self.history:
                    writer.writerow([r.timestamp_utc, r.sampled_s - self.origin, r.source, r.value, r.units, r.status, r.identity])
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Temperature export failed', str(exc))
