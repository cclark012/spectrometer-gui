from __future__ import annotations

import sys

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, Signal, Slot

from controllers.device_controller import DeviceController
from controllers.laser_controller import LaserController
from core.settings import AcquisitionSettings, DeviceConfig, PowerMonitorSettings, SNRSettings


class InstrumentRuntime(QObject):
    """Own the hardware worker objects, their threads, and request routing.

    The runtime lives in the GUI thread. Public methods only emit queued requests;
    all blocking instrument operations stay in the appropriate worker thread.
    """

    # Device-controller results.
    connected = Signal(str)
    connection_failed = Signal(str)
    spectrum_ready = Signal(object)
    acquisition_failed = Signal(str)
    power_ready = Signal(object)
    power_read_complete = Signal(str, object)
    power_read_failed = Signal(str, str)
    power_meter_wavelength_ready = Signal(int)
    spectrometer_info_ready = Signal(object)
    spectrometer_capabilities_ready = Signal(object)
    spectrometer_temperature_ready = Signal(float)
    background_ready = Signal(object)
    background_cleared = Signal()
    device_status = Signal(str)
    device_error = Signal(str)

    # Laser-controller results.
    lasers_ready = Signal(object)
    laser_power_set_complete = Signal(str, int, float)
    laser_enabled_set_complete = Signal(str, int, bool)
    laser_cdrh_set_complete = Signal(str, int, bool)
    laser_power_set_failed = Signal(str, int, str)
    laser_enabled_set_failed = Signal(str, int, str)
    laser_cdrh_set_failed = Signal(str, int, str)
    laser_status = Signal(str)
    laser_error = Signal(str)

    # Requests routed to DeviceController.
    _acquire_requested = Signal(object)
    _power_poll_requested = Signal()
    _power_settings_requested = Signal(object)
    _power_meter_wavelength_requested = Signal(int)
    _power_read_once_requested = Signal(str)
    _background_capture_requested = Signal(object)
    _background_clear_requested = Signal()
    _tec_target_requested = Signal(float)
    _tec_enabled_requested = Signal(bool)
    _snr_settings_requested = Signal(object)
    _spectrometer_temperature_requested = Signal()
    _spectrometer_capabilities_requested = Signal()

    # Requests routed to LaserController.
    _laser_refresh_requested = Signal()
    _laser_power_requested = Signal(str, int, float)
    _laser_enabled_requested = Signal(str, int, bool)
    _laser_disable_all_requested = Signal()
    _laser_cdrh_requested = Signal(str, int, bool)

    def __init__(self, config: DeviceConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.device_thread = QThread(self)
        self.laser_thread = QThread(self)
        self.device_controller = DeviceController(config)
        self.laser_controller = LaserController(
            emulate=bool(config.emulate_lasers),
            fallback_emulator=bool(config.laser_fallback_emulator),
            candidate_ports=config.obis_ports,
        )
        self.device_controller.moveToThread(self.device_thread)
        self.laser_controller.moveToThread(self.laser_thread)

        self._wire_device_controller()
        self._wire_laser_controller()
        self._started = False

    def _wire_device_controller(self) -> None:
        controller = self.device_controller
        queued = Qt.ConnectionType.QueuedConnection

        self.device_thread.started.connect(controller.connect_devices, queued)
        self._acquire_requested.connect(controller.acquire, queued)
        self._power_poll_requested.connect(controller.poll_power, queued)
        self._power_settings_requested.connect(controller.set_power_monitor_settings, queued)
        self._power_meter_wavelength_requested.connect(
            controller.set_power_meter_wavelength_nm,
            queued,
        )
        self._power_read_once_requested.connect(controller.read_power_once, queued)
        self._background_capture_requested.connect(controller.capture_background, queued)
        self._background_clear_requested.connect(controller.clear_background, queued)
        self._tec_target_requested.connect(controller.set_tec_target_c, queued)
        self._tec_enabled_requested.connect(controller.set_tec_enabled, queued)
        self._snr_settings_requested.connect(controller.set_snr_settings, queued)
        self._spectrometer_temperature_requested.connect(
            controller.query_spectrometer_temperature,
            queued,
        )
        self._spectrometer_capabilities_requested.connect(
            controller.query_spectrometer_capabilities,
            queued,
        )

        controller.connected.connect(self.connected.emit)
        controller.connection_failed.connect(self.connection_failed.emit)
        controller.spectrum_ready.connect(self.spectrum_ready.emit)
        controller.acquisition_failed.connect(self.acquisition_failed.emit)
        controller.power_ready.connect(self.power_ready.emit)
        controller.power_read_complete.connect(self.power_read_complete.emit)
        controller.power_read_failed.connect(self.power_read_failed.emit)
        controller.power_meter_wavelength_ready.connect(
            self.power_meter_wavelength_ready.emit
        )
        controller.spectrometer_info_ready.connect(self.spectrometer_info_ready.emit)
        controller.spectrometer_capabilities_ready.connect(
            self.spectrometer_capabilities_ready.emit
        )
        controller.spectrometer_temperature_ready.connect(
            self.spectrometer_temperature_ready.emit
        )
        controller.background_ready.connect(self.background_ready.emit)
        controller.background_cleared.connect(self.background_cleared.emit)
        controller.status.connect(self.device_status.emit)
        controller.error.connect(self.device_error.emit)

    def _wire_laser_controller(self) -> None:
        controller = self.laser_controller
        queued = Qt.ConnectionType.QueuedConnection

        self.laser_thread.started.connect(controller.refresh, queued)
        self._laser_refresh_requested.connect(controller.refresh, queued)
        self._laser_power_requested.connect(controller.set_power_w, queued)
        self._laser_enabled_requested.connect(controller.set_enabled, queued)
        self._laser_disable_all_requested.connect(controller.disable_all, queued)
        self._laser_cdrh_requested.connect(controller.set_cdrh_delay, queued)

        controller.lasers_ready.connect(self.lasers_ready.emit)
        controller.power_set_complete.connect(self.laser_power_set_complete.emit)
        controller.enabled_set_complete.connect(self.laser_enabled_set_complete.emit)
        controller.cdrh_set_complete.connect(self.laser_cdrh_set_complete.emit)
        controller.power_set_failed.connect(self.laser_power_set_failed.emit)
        controller.enabled_set_failed.connect(self.laser_enabled_set_failed.emit)
        controller.cdrh_set_failed.connect(self.laser_cdrh_set_failed.emit)
        controller.status.connect(self.laser_status.emit)
        controller.error.connect(self.laser_error.emit)

    def start(self) -> None:
        """Start worker threads after all GUI-facing signals are connected."""

        if self._started:
            return
        self._started = True
        self.device_thread.start()
        self.laser_thread.start()

    # -------------------------------------------------------------- device requests

    @Slot(object)
    def acquire(self, settings: AcquisitionSettings) -> None:
        self._acquire_requested.emit(settings)

    @Slot()
    def poll_power(self) -> None:
        self._power_poll_requested.emit()

    @Slot(object)
    def set_power_monitor_settings(self, settings: PowerMonitorSettings) -> None:
        self._power_settings_requested.emit(settings)

    @Slot(int)
    def set_power_meter_wavelength_nm(self, wavelength_nm: int) -> None:
        self._power_meter_wavelength_requested.emit(int(wavelength_nm))

    @Slot(str)
    def read_power_once(self, tag: str) -> None:
        self._power_read_once_requested.emit(str(tag))

    @Slot(object)
    def capture_background(self, settings: AcquisitionSettings) -> None:
        self._background_capture_requested.emit(settings)

    @Slot()
    def clear_background(self) -> None:
        self._background_clear_requested.emit()

    @Slot(float)
    def set_tec_target_c(self, temperature_c: float) -> None:
        self._tec_target_requested.emit(float(temperature_c))

    @Slot(bool)
    def set_tec_enabled(self, enabled: bool) -> None:
        self._tec_enabled_requested.emit(bool(enabled))

    @Slot(object)
    def set_snr_settings(self, settings: SNRSettings) -> None:
        self._snr_settings_requested.emit(settings)

    @Slot()
    def query_spectrometer_temperature(self) -> None:
        self._spectrometer_temperature_requested.emit()

    @Slot()
    def query_spectrometer_capabilities(self) -> None:
        self._spectrometer_capabilities_requested.emit()

    # --------------------------------------------------------------- laser requests

    @Slot()
    def refresh_lasers(self) -> None:
        self._laser_refresh_requested.emit()

    @Slot(str, int, float)
    def set_laser_power_w(self, port: str, channel: int, power_w: float) -> None:
        self._laser_power_requested.emit(str(port), int(channel), float(power_w))

    @Slot(str, int, bool)
    def set_laser_enabled(self, port: str, channel: int, enabled: bool) -> None:
        self._laser_enabled_requested.emit(str(port), int(channel), bool(enabled))

    @Slot()
    def disable_all_lasers(self) -> None:
        self._laser_disable_all_requested.emit()

    @Slot(str, int, bool)
    def set_laser_cdrh_delay(self, port: str, channel: int, enabled: bool) -> None:
        self._laser_cdrh_requested.emit(str(port), int(channel), bool(enabled))

    # -------------------------------------------------------------------- lifecycle

    @staticmethod
    def _shutdown_worker(
        thread: QThread,
        worker: QObject,
        timeout_ms: int,
    ) -> None:
        if not thread.isRunning():
            return
        try:
            QMetaObject.invokeMethod(
                worker,
                "shutdown",
                Qt.ConnectionType.BlockingQueuedConnection,
            )
        except Exception:
            pass
        thread.quit()
        if not thread.wait(int(timeout_ms)):
            print(
                f"Worker thread did not stop within {timeout_ms} ms.",
                file=sys.stderr,
            )

    def shutdown(self) -> None:
        self._shutdown_worker(self.device_thread, self.device_controller, 5000)
        self._shutdown_worker(self.laser_thread, self.laser_controller, 3000)
        self._started = False
