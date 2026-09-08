from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, Signal, Slot

from controllers.device_controller import DeviceController
from controllers.laser_controller import LaserController
from core.records import InstrumentConnectionState, PowerSnapshot, SpectrumRecord
from core.settings import AcquisitionSettings, DeviceConfig, PowerMonitorSettings, SNRSettings

logger = logging.getLogger(__name__)


class InstrumentRuntime(QObject):
    """Own independent hardware workers and coordinate cross-instrument tasks.

    The runtime lives in the GUI thread. Each instrument family has a separate
    worker thread, so a slow Newport request cannot delay a spectrum (and vice
    versa). The only cross-instrument operation here is optional power sampling
    immediately before and after a spectrum.
    """

    connected = Signal(str)
    connection_failed = Signal(str)
    spectrometer_connection_changed = Signal(object)
    power_meter_connection_changed = Signal(object)
    laser_connection_changed = Signal(object)

    spectrum_ready = Signal(object)
    acquisition_failed = Signal(str)
    power_ready = Signal(object)
    power_read_complete = Signal(str, object)
    power_read_failed = Signal(str, str)
    power_meter_wavelength_ready = Signal(int)
    spectrometer_info_ready = Signal(object)
    spectrometer_capabilities_ready = Signal(object)
    spectrometer_temperature_ready = Signal(float)
    spectrometer_prepared = Signal()
    spectrometer_prepare_failed = Signal(str)
    background_ready = Signal(object)
    background_cleared = Signal()
    background_failed = Signal(str)
    device_status = Signal(str)
    device_error = Signal(str)

    lasers_ready = Signal(object)
    laser_power_set_complete = Signal(str, int, float)
    laser_enabled_set_complete = Signal(str, int, bool)
    laser_cdrh_set_complete = Signal(str, int, bool)
    laser_power_set_failed = Signal(str, int, str)
    laser_enabled_set_failed = Signal(str, int, str)
    laser_cdrh_set_failed = Signal(str, int, str)
    laser_status = Signal(str)
    laser_error = Signal(str)

    # Spectrometer-worker requests.
    _acquire_requested = Signal(object)
    _background_capture_requested = Signal(object)
    _background_clear_requested = Signal()
    _tec_target_requested = Signal(float)
    _tec_enabled_requested = Signal(bool)
    _snr_settings_requested = Signal(object)
    _spectrometer_temperature_requested = Signal()
    _spectrometer_capabilities_requested = Signal()
    _spectrometer_configuration_requested = Signal(object)
    _spectrometer_prepare_requested = Signal(object)
    _connect_spectrometer_requested = Signal()
    _connect_spectrometer_selection_requested = Signal(str, str)
    _disconnect_spectrometer_requested = Signal()

    # Power-worker requests.
    _power_poll_requested = Signal()
    _power_settings_requested = Signal(object)
    _power_meter_wavelength_requested = Signal(int)
    _power_read_once_requested = Signal(str)
    _connect_power_meter_requested = Signal()
    _connect_power_meter_selection_requested = Signal(str)
    _disconnect_power_meter_requested = Signal()

    # Laser-worker requests.
    _laser_refresh_requested = Signal()
    _laser_power_requested = Signal(str, int, float)
    _laser_enabled_requested = Signal(str, int, bool)
    _laser_disable_all_requested = Signal()
    _laser_cdrh_requested = Signal(str, int, bool)
    _disconnect_lasers_requested = Signal()
    _connect_lasers_mode_requested = Signal(str)

    _INTERNAL_POWER_PREFIX = "__instrument_runtime_acquisition__"

    def __init__(self, config: DeviceConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config

        self.spectrometer_thread = QThread(self)
        self.power_thread = QThread(self)
        self.laser_thread = QThread(self)
        self.spectrometer_controller = DeviceController(config)
        self.power_controller = DeviceController(config)
        self.laser_controller = LaserController(
            emulate=bool(config.emulate_lasers),
            fallback_emulator=bool(config.laser_fallback_emulator),
            candidate_ports=config.obis_ports,
            mode=config.laser_mode,
        )

        # Compatibility aliases for code that used the original combined worker.
        self.device_thread = self.spectrometer_thread
        self.device_controller = self.spectrometer_controller

        self.spectrometer_controller.moveToThread(self.spectrometer_thread)
        self.power_controller.moveToThread(self.power_thread)
        self.laser_controller.moveToThread(self.laser_thread)

        self._connection_states: dict[str, InstrumentConnectionState] = {}
        self._power_meter_connected = False
        self._power_poll_pending = False
        self._acquisition_serial = 0
        self._acquisition_phase = "idle"
        self._acquisition_settings: AcquisitionSettings | None = None
        self._acquisition_pre_power = PowerSnapshot.missing()
        self._acquisition_record: SpectrumRecord | None = None
        self._internal_power_tag = ""

        self._wire_spectrometer_controller()
        self._wire_power_controller()
        self._wire_laser_controller()
        self._started = False

    def _wire_spectrometer_controller(self) -> None:
        controller = self.spectrometer_controller
        queued = Qt.ConnectionType.QueuedConnection

        self.spectrometer_thread.started.connect(
            controller.connect_spectrometer,
            queued,
        )
        self._acquire_requested.connect(controller.acquire, queued)
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
        self._spectrometer_configuration_requested.connect(
            controller.configure_spectrometer,
            queued,
        )
        self._spectrometer_prepare_requested.connect(
            controller.prepare_spectrometer,
            queued,
        )
        self._connect_spectrometer_requested.connect(
            controller.connect_spectrometer,
            queued,
        )
        self._connect_spectrometer_selection_requested.connect(
            controller.connect_spectrometer_selection,
            queued,
        )
        self._disconnect_spectrometer_requested.connect(
            controller.disconnect_spectrometer,
            queued,
        )

        controller.spectrometer_connection_changed.connect(
            self._on_spectrometer_connection_changed
        )
        controller.spectrum_ready.connect(self._on_spectrometer_ready)
        controller.acquisition_failed.connect(self._on_spectrometer_failed)
        controller.spectrometer_info_ready.connect(self.spectrometer_info_ready.emit)
        controller.spectrometer_capabilities_ready.connect(
            self.spectrometer_capabilities_ready.emit
        )
        controller.spectrometer_temperature_ready.connect(
            self.spectrometer_temperature_ready.emit
        )
        controller.spectrometer_prepared.connect(self.spectrometer_prepared.emit)
        controller.spectrometer_prepare_failed.connect(
            self.spectrometer_prepare_failed.emit
        )
        controller.background_ready.connect(self.background_ready.emit)
        controller.background_cleared.connect(self.background_cleared.emit)
        controller.background_failed.connect(self.background_failed.emit)
        controller.status.connect(self.device_status.emit)
        controller.error.connect(self.device_error.emit)

    def _wire_power_controller(self) -> None:
        controller = self.power_controller
        queued = Qt.ConnectionType.QueuedConnection

        self.power_thread.started.connect(controller.connect_power_meter, queued)
        self._power_poll_requested.connect(controller.poll_power, queued)
        self._power_settings_requested.connect(
            controller.set_power_monitor_settings,
            queued,
        )
        self._power_meter_wavelength_requested.connect(
            controller.set_power_meter_wavelength_nm,
            queued,
        )
        self._power_read_once_requested.connect(controller.read_power_once, queued)
        self._connect_power_meter_requested.connect(
            controller.connect_power_meter,
            queued,
        )
        self._connect_power_meter_selection_requested.connect(
            controller.connect_power_meter_selection,
            queued,
        )
        self._disconnect_power_meter_requested.connect(
            controller.disconnect_power_meter,
            queued,
        )

        controller.power_meter_connection_changed.connect(
            self._on_power_meter_connection_changed
        )
        controller.power_ready.connect(self.power_ready.emit)
        controller.power_poll_finished.connect(self._on_power_poll_finished)
        controller.power_read_complete.connect(self._on_power_read_complete)
        controller.power_read_failed.connect(self._on_power_read_failed)
        controller.power_meter_wavelength_ready.connect(
            self.power_meter_wavelength_ready.emit
        )
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
        self._disconnect_lasers_requested.connect(controller.disconnect_all_boxes, queued)
        self._connect_lasers_mode_requested.connect(controller.connect_mode, queued)

        controller.connection_changed.connect(self.laser_connection_changed.emit)
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
        """Start workers after all GUI-facing signals are connected."""

        if self._started:
            return
        self._started = True
        self.spectrometer_thread.start()
        self.power_thread.start()
        self.laser_thread.start()

    def _publish_device_connection_summary(self) -> None:
        if not {"spectrometer", "power_meter"}.issubset(self._connection_states):
            return
        states = tuple(
            self._connection_states[key]
            for key in ("spectrometer", "power_meter")
        )
        descriptions = [state.description for state in states if state.description]
        message = "; ".join(descriptions)
        if any(state.connected for state in states):
            self.connected.emit(message)
        else:
            self.connection_failed.emit("No spectrometer or power meter connected.")

    @Slot(object)
    def _on_spectrometer_connection_changed(
        self,
        state: InstrumentConnectionState,
    ) -> None:
        self._connection_states["spectrometer"] = state
        self.spectrometer_connection_changed.emit(state)
        self._publish_device_connection_summary()

    @Slot(object)
    def _on_power_meter_connection_changed(
        self,
        state: InstrumentConnectionState,
    ) -> None:
        self._power_meter_connected = bool(state.connected)
        self._connection_states["power_meter"] = state
        self.power_meter_connection_changed.emit(state)
        self._publish_device_connection_summary()

    def _new_internal_power_tag(self, phase: str) -> str:
        self._acquisition_serial += 1
        return f"{self._INTERNAL_POWER_PREFIX}:{phase}:{self._acquisition_serial}"

    def _reset_acquisition(self) -> None:
        self._acquisition_phase = "idle"
        self._acquisition_settings = None
        self._acquisition_pre_power = PowerSnapshot.missing()
        self._acquisition_record = None
        self._internal_power_tag = ""

    def _fail_acquisition(self, message: str) -> None:
        self._reset_acquisition()
        self.acquisition_failed.emit(str(message))

    # -------------------------------------------------------------- device requests

    @Slot(object)
    def acquire(self, settings: AcquisitionSettings) -> None:
        if self._acquisition_phase != "idle":
            self.acquisition_failed.emit(
                "A spectrum is already pending in the instrument runtime."
            )
            return

        self._acquisition_settings = replace(settings)
        self._acquisition_pre_power = PowerSnapshot.missing()
        self._acquisition_record = None
        if settings.measure_power and self._power_meter_connected:
            self._acquisition_phase = "before_power"
            self._internal_power_tag = self._new_internal_power_tag("before")
            self._power_read_once_requested.emit(self._internal_power_tag)
            return

        self._acquisition_phase = "spectrum"
        self._acquire_requested.emit(self._acquisition_settings)

    @Slot(str, object)
    def _on_power_read_complete(self, tag: str, snapshot: PowerSnapshot) -> None:
        if str(tag) != self._internal_power_tag:
            self.power_read_complete.emit(str(tag), snapshot)
            return

        if self._acquisition_phase == "before_power":
            settings = self._acquisition_settings
            if settings is None:
                self._fail_acquisition("Lost the pending spectrum settings.")
                return
            self._acquisition_pre_power = snapshot
            self._internal_power_tag = ""
            self._acquisition_phase = "spectrum"
            self._acquire_requested.emit(settings)
            return

        if self._acquisition_phase == "after_power":
            record = self._acquisition_record
            if record is None:
                self._fail_acquisition("Lost the pending spectrum result.")
                return
            record.p_after = snapshot
            self._reset_acquisition()
            self.spectrum_ready.emit(record)

    @Slot(str, str)
    def _on_power_read_failed(self, tag: str, message: str) -> None:
        if str(tag) != self._internal_power_tag:
            self.power_read_failed.emit(str(tag), str(message))
            return
        phase = "before" if self._acquisition_phase == "before_power" else "after"
        self._fail_acquisition(
            f"Power measurement {phase} spectrum failed: {message}"
        )

    @Slot(object)
    def _on_spectrometer_ready(self, record: SpectrumRecord) -> None:
        if self._acquisition_phase != "spectrum":
            logger.error(
                "Received a spectrum while runtime acquisition phase was %s.",
                self._acquisition_phase,
            )
            return

        settings = self._acquisition_settings
        if settings is None:
            self._fail_acquisition("Lost the pending spectrum settings.")
            return
        record.p_before = self._acquisition_pre_power

        if settings.measure_power and self._power_meter_connected:
            self._acquisition_record = record
            self._acquisition_phase = "after_power"
            self._internal_power_tag = self._new_internal_power_tag("after")
            self._power_read_once_requested.emit(self._internal_power_tag)
            return

        self._reset_acquisition()
        self.spectrum_ready.emit(record)

    @Slot(str)
    def _on_spectrometer_failed(self, message: str) -> None:
        self._fail_acquisition(str(message))

    @Slot()
    def poll_power(self) -> None:
        if self._power_poll_pending:
            return
        self._power_poll_pending = True
        self._power_poll_requested.emit()

    @Slot()
    def _on_power_poll_finished(self) -> None:
        self._power_poll_pending = False

    @Slot(object)
    def set_power_monitor_settings(self, settings: PowerMonitorSettings) -> None:
        self._power_settings_requested.emit(settings)

    @Slot(int)
    def set_power_meter_wavelength_nm(self, wavelength_nm: int) -> None:
        self._power_meter_wavelength_requested.emit(int(wavelength_nm))

    @Slot(str)
    def read_power_once(self, tag: str) -> None:
        self._power_read_once_requested.emit(str(tag))

    @Slot()
    def connect_power_meter(self) -> None:
        self._connect_power_meter_requested.emit()

    @Slot(str)
    def connect_power_meter_selection(self, mode: str) -> None:
        try:
            self.config.select_power_meter(str(mode))
        except ValueError as exc:
            self.device_error.emit(str(exc))
            return
        self._connect_power_meter_selection_requested.emit(str(mode))

    @Slot()
    def disconnect_power_meter(self) -> None:
        self._disconnect_power_meter_requested.emit()

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

    @Slot(object)
    def configure_spectrometer(self, values: object) -> None:
        self._spectrometer_configuration_requested.emit(values)

    @Slot(object)
    def prepare_spectrometer(self, settings: AcquisitionSettings) -> None:
        self._spectrometer_prepare_requested.emit(replace(settings))

    @Slot()
    def connect_spectrometer(self) -> None:
        self._connect_spectrometer_requested.emit()

    @Slot(str, str)
    def connect_spectrometer_selection(self, mode: str, backend: str) -> None:
        try:
            self.config.select_spectrometer(str(mode), str(backend))
        except ValueError as exc:
            self.device_error.emit(str(exc))
            return
        self._connect_spectrometer_selection_requested.emit(str(mode), str(backend))

    @Slot()
    def disconnect_spectrometer(self) -> None:
        self._disconnect_spectrometer_requested.emit()

    # --------------------------------------------------------------- laser requests

    @Slot()
    def refresh_lasers(self) -> None:
        self._laser_refresh_requested.emit()

    @Slot(str)
    def connect_lasers_mode(self, mode: str) -> None:
        try:
            self.config.select_lasers(str(mode))
        except ValueError as exc:
            self.laser_error.emit(str(exc))
            return
        self._connect_lasers_mode_requested.emit(str(mode))

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

    @Slot()
    def disconnect_lasers(self) -> None:
        self._disconnect_lasers_requested.emit()

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
            logger.exception("Could not invoke instrument-worker shutdown.")
        thread.quit()
        if not thread.wait(int(timeout_ms)):
            logger.error("Worker thread did not stop within %d ms.", timeout_ms)

    def shutdown(self) -> None:
        self._reset_acquisition()
        self._shutdown_worker(
            self.spectrometer_thread,
            self.spectrometer_controller,
            5000,
        )
        self._shutdown_worker(self.power_thread, self.power_controller, 5000)
        self._shutdown_worker(self.laser_thread, self.laser_controller, 3000)
        self._started = False
