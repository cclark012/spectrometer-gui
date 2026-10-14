"""Event-driven, leased ordinary scan workflow with draining cancellation."""
import time
import uuid
from core.time_utils import utc_now_iso
from dataclasses import asdict, replace
from PySide6.QtCore import QObject, QTimer, Signal
from planning.parameter_scan import TemperatureGate

class ParameterScanCoordinator(QObject):
    acquire_requested = Signal()
    configure_requested = Signal(object)
    temperature_requested = Signal(str)
    save_requested = Signal(object)
    status = Signal(str)
    finished = Signal(str)
    summary_ready = Signal(object)
    disable_lasers_requested = Signal()

    def __init__(self, parent=None, clock=time.perf_counter):
        super().__init__(parent)
        self.clock = clock
        self.active = False
        self.phase = 'idle'
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.readings = {}
        self.cancel_reason = ''

    def start(self, plan, settings, original_center=None):
        if self.active:
            raise RuntimeError('A parameter scan is already running')
        if plan.kind == 'wavelength' and original_center is None:
            raise ValueError('A verified Kymera center readback is required')
        self.plan, self.template = (plan, replace(settings))
        self.original_center = original_center
        self.points = plan.points
        self.index = 0
        self.origin = self.clock()
        self.scan_id = uuid.uuid4().hex
        self.started_utc = utc_now_iso()
        self.cancel_reason = ''
        self.readings = {}
        self.active = True
        self.timer.start()
        self._begin_point()

    def _begin_point(self):
        if self.cancel_reason or self.index >= len(self.points):
            self._finish()
            return
        self.value = self.points[self.index]
        self.point_started = self.clock()
        self.gate = TemperatureGate()
        self.trigger = None
        self.last_query = -float('inf')
        self.status.emit(f'{self.plan.kind}: point {self.index + 1}/{len(self.points)}, target {self.value:g}')
        if self.plan.kind == 'wavelength':
            self.phase = 'configure'
            self.configure_requested.emit({'spectrograph': {'center_wavelength_nm': self.value}})
        else:
            self.phase = 'waiting'
            self.ready_at = self.clock() + self.plan.settle_s

    def configuration_applied(self, capabilities):
        if not self.active or self.phase not in {'configure', 'restore'}:
            return
        if self.phase == 'restore':
            self._complete()
        elif self.cancel_reason:
            self._finish()
        else:
            self.phase = 'waiting'
            self.ready_at = self.clock() + self.plan.settle_s

    def configuration_failed(self, message):
        if not self.active or self.phase not in {'configure', 'restore'}:
            return
        restoring = self.phase == 'restore'
        self.cancel_reason = ('Restore failed: ' if restoring else 'Configuration failed: ') + str(message)
        self.disable_lasers_requested.emit()
        if restoring:
            self._complete()
        else:
            self._finish()

    def accept_temperature(self, reading):
        self.readings[reading.source] = reading

    def tick(self):
        if not self.active or self.phase != 'waiting':
            return
        now = self.clock()
        if self.plan.kind == 'temperature' and now - self.point_started >= self.plan.timeout_s:
            self.abort('Temperature wait timed out')
            return
        if now < self.ready_at:
            return
        if self.plan.kind == 'time':
            if now < self.origin + self.value:
                return
        if self.plan.kind == 'temperature':
            if now - self.last_query >= 1.0:
                self.last_query = now
                self.temperature_requested.emit(self.plan.temperature_source)
            reading = self.readings.get(self.plan.temperature_source)
            if reading is not None and reading.sampled_s < self.point_started:
                reading = None
            if not self.gate.update(reading, target=self.value, now=now, tolerance=self.plan.tolerance, dwell=self.plan.dwell_s, max_age=self.plan.max_age_s):
                return
            self.trigger = reading
        self.requested_at = now
        self.phase = 'acquire'
        self.acquire_requested.emit()

    def acquisition_settings(self):
        settings = replace(self.template, continuous_session='')
        if self.plan.kind == 'exposure':
            settings.integration_ms = int(self.value)
        return settings

    def spectrum_ready(self, record):
        if not self.active or self.phase != 'acquire':
            return
        metadata = dict(scan_id=self.scan_id, kind=self.plan.kind, index=self.index + 1, count=len(self.points), target=self.value, units={'exposure': 'ms', 'wavelength': 'nm', 'time': 's', 'temperature': 'degC' if self.plan.temperature_source == 'ccd' else 'K'}[self.plan.kind], requested_elapsed_s=self.requested_at - self.origin, completed_elapsed_s=self.clock() - self.origin, settle_s=self.plan.settle_s)
        if self.plan.kind == 'time':
            metadata.update(scheduled_elapsed_s=self.value, request_lateness_s=max(0.0, self.requested_at - self.origin - self.value), overrun_policy='serialize_all_points')
        if self.trigger:
            metadata.update(temperature_trigger=asdict(self.trigger), trigger_age_at_request_s=self.requested_at - self.trigger.sampled_s, tolerance=self.plan.tolerance, dwell_s=self.plan.dwell_s)
        if self.cancel_reason:
            metadata['termination_requested'] = self.cancel_reason
        record.parameter_scan = metadata
        self.save_requested.emit(record)
        self.index += 1
        self._begin_point()

    def acquisition_failed(self, message):
        if self.active and self.phase == 'acquire':
            self.cancel_reason = 'Acquisition failed: ' + str(message)
            self.disable_lasers_requested.emit()
            self._finish()

    def abort(self, reason='Aborted by user'):
        if not self.active:
            return
        self.cancel_reason = reason
        self.disable_lasers_requested.emit()
        self.status.emit(reason + '; waiting for any in-flight operation before releasing ownership')
        if self.phase not in {'configure', 'acquire', 'restore'}:
            self._finish()

    def disconnected(self, reason):
        if self.active:
            self.cancel_reason = reason
            self.disable_lasers_requested.emit()
            self._complete()

    def _finish(self):
        if self.plan.lasers_off_at_end or self.cancel_reason:
            self.disable_lasers_requested.emit()
        if self.plan.kind == 'wavelength' and self.plan.restore:
            self.phase = 'restore'
            self.configure_requested.emit({'spectrograph': {'center_wavelength_nm': self.original_center}})
        else:
            self._complete()

    def _complete(self):
        self.timer.stop()
        self.active = False
        self.phase = 'idle'
        summary = dict(file_type='parameter_scan_summary', schema_version=1, scan_id=self.scan_id, started_utc=self.started_utc, finished_utc=utc_now_iso(), plan=asdict(self.plan), completed_acquisitions=self.index, requested_acquisitions=len(self.points), termination=self.cancel_reason or 'completed', original_center_nm=self.original_center, elapsed_s=self.clock() - self.origin)
        self.summary_ready.emit(summary)
        self.finished.emit(self.cancel_reason or f'Completed {self.index} spectra')
