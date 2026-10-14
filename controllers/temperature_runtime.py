"""Dedicated optional sensor worker; no heater/setpoint/control writes."""
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt, QMetaObject
from devices.lakeshore_335 import LakeShore335, EmulatedLakeShore335

class TemperatureWorker(QObject):
    readings = Signal(object)
    state = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.adapter = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)

    @Slot(str, str)
    def connect_sensor(self, mode, port):
        self.disconnect_sensor()
        try:
            self.adapter = EmulatedLakeShore335() if mode == 'emulated' else LakeShore335(port)
            self.state.emit(True, self.adapter.identity)
            self.timer.start()
            self.poll()
        except Exception as exc:
            self.disconnect_sensor()
            self.state.emit(False, str(exc))

    @Slot()
    def poll(self):
        if self.adapter is None:
            return
        try:
            self.readings.emit(self.adapter.read())
        except Exception as exc:
            self.disconnect_sensor()
            self.state.emit(False, str(exc))

    @Slot()
    def disconnect_sensor(self):
        self.timer.stop()
        if self.adapter:
            try:
                self.adapter.close()
            except Exception as exc:
                self.state.emit(False, 'Disconnect: ' + str(exc))
            finally:
                self.adapter = None
        self.state.emit(False, 'Disconnected')

class TemperatureRuntime(QObject):
    readings = Signal(object)
    state = Signal(bool, str)
    connect_requested = Signal(str, str)
    disconnect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = QThread(self)
        self.worker = TemperatureWorker()
        self.worker.moveToThread(self.thread)
        self.connect_requested.connect(self.worker.connect_sensor, Qt.ConnectionType.QueuedConnection)
        self.disconnect_requested.connect(self.worker.disconnect_sensor, Qt.ConnectionType.QueuedConnection)
        self.worker.readings.connect(self.readings.emit)
        self.worker.state.connect(self.state.emit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

    def shutdown(self):
        if self.thread.isRunning():
            QMetaObject.invokeMethod(self.worker, 'disconnect_sensor', Qt.ConnectionType.BlockingQueuedConnection)
            self.thread.quit()
            self.thread.wait()
