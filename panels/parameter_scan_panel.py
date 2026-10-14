from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton, QLabel
from planning.parameter_scan import ParameterScanPlan, generate_scan_values

class ParameterScanPanel(QWidget):
    start_requested = Signal()
    abort_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.controls = QWidget()
        form = QFormLayout(self.controls)
        self.kind = QComboBox()
        for title, key in [('Exposure (ms)', 'exposure'), ('Kymera center (nm)', 'wavelength'), ('Scheduled times (s)', 'time'), ('Temperature targets', 'temperature')]:
            self.kind.addItem(title, key)
        self.spacing = QComboBox()
        self.spacing.addItem('Custom list', 'custom')
        self.spacing.addItem('Linear', 'linear')
        self.spacing.addItem('Logarithmic', 'log')
        self.first = self.spin(-273.15, 604800000, 0)
        self.last = self.spin(-273.15, 604800000, 100)
        self.count = QSpinBox()
        self.count.setRange(1, 10000)
        self.count.setValue(11)
        self.values = QLineEdit('10, 50, 100')
        self.values.setToolTip('Comma-separated points; time values are offsets from Start. Up to 10000 acquisitions.')
        self.repeats = QSpinBox()
        self.repeats.setRange(1, 10000)
        self.settle = self.spin(0, 3600, 0)
        self.source = QComboBox()
        for title, key in [('CCD (°C)', 'ccd'), ('Lake Shore A (K)', 'lakeshore_A'), ('Lake Shore B (K)', 'lakeshore_B')]:
            self.source.addItem(title, key)
        self.tolerance = self.spin(0, 100, 0.2)
        self.dwell = self.spin(0, 86400, 5)
        self.timeout = self.spin(1, 604800, 600)
        self.age = self.spin(0.1, 60, 5)
        self.restore = QCheckBox('Restore original Kymera center at end')
        self.restore.setChecked(True)
        self.off = QCheckBox('Turn lasers off at end')
        self.off.setChecked(True)
        for title, widget in [('Parameter', self.kind), ('Spacing', self.spacing), ('Custom points', self.values), ('Start', self.first), ('Stop', self.last), ('Point count', self.count), ('Repeats per point', self.repeats), ('Settle (s)', self.settle), ('Temperature source', self.source), ('Temperature tolerance', self.tolerance), ('In-band dwell (s)', self.dwell), ('Timeout per point (s)', self.timeout), ('Maximum reading age (s)', self.age), ('', self.restore), ('', self.off)]:
            form.addRow(title, widget)
        root.addWidget(self.controls)
        note = QLabel('Ordinary spectra; each spectrum is autosaved. Laser emission is held at its current state during the scan. Temperature targets are observed, not commanded. Scheduled overruns run late and retain every point.')
        note.setWordWrap(True)
        root.addWidget(note)
        self.start_button = QPushButton('Start scan')
        self.start_button.clicked.connect(lambda _checked=False: self.start_requested.emit())
        self.abort_button = QPushButton('Abort')
        self.abort_button.clicked.connect(lambda _checked=False: self.abort_requested.emit())
        self.abort_button.setEnabled(False)
        root.addWidget(self.start_button)
        root.addWidget(self.abort_button)
        self.status = QLabel('Idle')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        root.addStretch()
        self.kind.currentIndexChanged.connect(self._mode_changed)
        self.spacing.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()

    @staticmethod
    def spin(lo, hi, value):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(3)
        s.setValue(value)
        return s

    def _mode_changed(self):
        kind = self.kind.currentData()
        custom = self.spacing.currentData() == 'custom'
        self.values.setEnabled(custom)
        for widget in (self.first, self.last, self.count):
            widget.setEnabled(not custom)
        self.repeats.setEnabled(kind != 'time')
        if kind == 'time':
            self.repeats.setValue(1)
        for w in (self.source, self.tolerance, self.dwell, self.timeout, self.age):
            w.setEnabled(kind == 'temperature')
        self.restore.setEnabled(kind == 'wavelength')

    def plan(self):
        if self.spacing.currentData() == 'custom':
            values = tuple((float(v.strip()) for v in self.values.text().split(',') if v.strip()))
        else:
            values = generate_scan_values(self.first.value(), self.last.value(), self.count.value(), self.spacing.currentData())
            if self.kind.currentData() == 'exposure':
                values = tuple((float(round(v)) for v in values))
        return ParameterScanPlan(self.kind.currentData(), values, self.repeats.value(), self.settle.value(), self.source.currentData(), self.tolerance.value(), self.dwell.value(), self.timeout.value(), self.age.value(), self.restore.isChecked(), self.off.isChecked())

    def set_busy(self, owner, connected):
        active = owner == 'parameter_scan'
        self.controls.setEnabled(owner is None)
        self.start_button.setEnabled(owner is None and connected)
        self.abort_button.setEnabled(active)
