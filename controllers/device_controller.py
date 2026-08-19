from __future__ import annotations

import time
import traceback
from dataclasses import replace
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from core.records import (
    BackgroundSpectrum,
    InstrumentConnectionState,
    PowerSnapshot,
    SpectralAcquisition,
    SpectrometerCapabilities,
    SpectrometerInfo,
    SpectrumRecord,
)
from core.settings import AcquisitionSettings, DeviceConfig, PowerMonitorSettings, SNRSettings
from core.time_utils import utc_now_iso
from devices.emulated_power_meter import EmulatedPowerMeter
from devices.emulated_spectrometer import EmulatedSpectrometer
from devices.protocols import PowerMeterAdapter, SpectrometerAdapter
from devices.qepro_adapter import QEProSpectrometer, SpectrometerCommunicationError
from processing.background import BackgroundCorrector
from processing.smoothing import boxcar_smooth
from processing.snr import estimate_snr
from validation.power_validation import power_snapshot_valid


class DeviceController(QObject):
    acquisition_failed = Signal(str)
    connected = Signal(str)
    connection_failed = Signal(str)
    spectrum_ready = Signal(object)
    background_ready = Signal(object)
    background_cleared = Signal()
    background_failed = Signal(str)
    power_ready = Signal(object)
    power_read_complete = Signal(str, object)
    power_read_failed = Signal(str, str)
    power_meter_wavelength_ready = Signal(int)
    power_meter_connection_changed = Signal(object)
    spectrometer_info_ready = Signal(object)
    spectrometer_capabilities_ready = Signal(object)
    spectrometer_temperature_ready = Signal(float)
    spectrometer_connection_changed = Signal(object)
    status = Signal(str)
    error = Signal(str)

    def __init__(self, config: DeviceConfig) -> None:
        super().__init__()
        self.config = config

        self.spec: SpectrometerAdapter | None = None
        self.pm: PowerMeterAdapter | None = None
        self.spec_available = False
        self.power_available = False

        self.power_monitor_settings = PowerMonitorSettings()
        self.background = BackgroundCorrector()
        self._last_invalid_power_status_s = 0.0
        self._capabilities: SpectrometerCapabilities | None = None
        self._info: SpectrometerInfo | None = None

        self.snr_settings = SNRSettings()
        self._snr_acquisition_counter = 0

    @Slot()
    def connect_devices(self) -> None:
        spectrometer_state = (
            self._connect_spectrometer_now()
        )
        power_state = self._connect_power_meter_now()

        states = [
            spectrometer_state,
            power_state,
        ]

        messages = [
            state.description
            for state in states
            if state.description
        ]

        errors = [
            state.error
            for state in states
            if state.error
        ]

        if not any(state.connected for state in states):
            self.connection_failed.emit(
                "No spectrometer or power meter connected."
                + (
                    "\n\n" + "\n".join(errors)
                    if errors
                    else ""
                )
            )
            return

        message = "; ".join(messages)

        if errors:
            message += (
                "\n\nUnavailable instrument(s):\n"
                + "\n".join(errors)
            )

        self.connected.emit(message)

    def _connect_spectrometer(
        self,
    ) -> tuple[SpectrometerAdapter | None, str, str]:
        try:
            if self.config.emulate:
                return EmulatedSpectrometer(), "Spectrometer: emulator", ""
            return QEProSpectrometer(), "Spectrometer: QEPro", ""
        except Exception:
            error = "Spectrometer connection failed:\n" + traceback.format_exc()

        if self.config.fallback_emulator:
            try:
                return (
                    EmulatedSpectrometer(),
                    "Spectrometer: emulator fallback",
                    error,
                )
            except Exception:
                error += "\nSpectrometer emulator fallback failed:\n" + traceback.format_exc()

        return None, "", error

    def _connect_spectrometer_now(
        self,
    ) -> InstrumentConnectionState:
        self._close_spectrometer()

        spectrometer, message, error = (
            self._connect_spectrometer()
        )

        self.spec = spectrometer
        self.spec_available = spectrometer is not None

        if self.spec_available:
            try:
                self._emit_spectrometer_info()
            except SpectrometerCommunicationError as exc:
                self._close_spectrometer()
                message = ""
                error = f"Spectrometer capability query failed: {exc}"

        state = InstrumentConnectionState(
            key="spectrometer",
            connected=self.spec_available,
            emulated=isinstance(
                spectrometer,
                EmulatedSpectrometer,
            ),
            description=message,
            error=error,
        )

        self.spectrometer_connection_changed.emit(state)
        return state

    def _connect_power_meter(self) -> tuple[PowerMeterAdapter | None, str, str]:
        try:
            if self.config.emulate:
                return EmulatedPowerMeter(), "Power meter: emulator", ""

            from devices.newport_2936r_dotnet import Newport2936R

            if self.config.newport_dll is None:
                raise RuntimeError("Real Newport mode requires newport_dll.")

            return (
                Newport2936R(
                    self.config.newport_dll,
                    channel=self.config.power_channel,
                    units=2,
                ),
                "Power meter: Newport 2936-R",
                "",
            )
        except Exception:
            error = "Power meter connection failed:\n" + traceback.format_exc()

        if self.config.fallback_emulator:
            try:
                return EmulatedPowerMeter(), "Power meter: emulator fallback", error
            except Exception:
                error += "\nPower meter emulator fallback failed:\n" + traceback.format_exc()

        return None, "", error

    def _connect_power_meter_now(
        self,
    ) -> InstrumentConnectionState:
        self._close_power_meter()

        meter, message, error = (
            self._connect_power_meter()
        )

        self.pm = meter
        self.power_available = meter is not None

        state = InstrumentConnectionState(
            key="power_meter",
            connected=self.power_available,
            emulated=isinstance(
                meter,
                EmulatedPowerMeter,
            ),
            description=message,
            error=error,
        )

        self.power_meter_connection_changed.emit(state)
        return state

    @Slot()
    def connect_spectrometer(self) -> None:
        self._connect_spectrometer_now()

    @Slot()
    def disconnect_spectrometer(self) -> None:
        self._close_spectrometer()

        self.spectrometer_connection_changed.emit(
            InstrumentConnectionState(
                key="spectrometer",
                connected=False,
                description="Spectrometer disconnected.",
            )
        )

        self.status.emit("Spectrometer disconnected.")

    @Slot(object)
    def set_snr_settings(self, settings: SNRSettings) -> None:
        self.snr_settings = replace(settings)

    @Slot(object)
    def capture_background(self, settings: AcquisitionSettings) -> None:
        try:
            acquisition = self._acquire_spectrometer(
                replace(settings, subtract_background=False)
            )
            integration_s = max(float(settings.integration_ms) * 1.0e-3, 1.0e-12)
            background = BackgroundSpectrum(
                timestamp_utc=utc_now_iso(),
                wavelengths_nm=acquisition.wavelengths_nm,
                counts_per_s=acquisition.intensities_counts / integration_s,
                integration_ms=int(settings.integration_ms),
                averages=int(settings.averages),
                correct_dark=bool(settings.correct_dark),
                correct_nonlinearity=bool(settings.correct_nonlinearity),
                averaging_mode=str(settings.averaging_mode),
            )
            self.background.set_background(background)
            self.background_ready.emit(background)
            self.status.emit("Background spectrum captured.")
        except SpectrometerCommunicationError as exc:
            self._handle_spectrometer_connection_loss(exc)
            self.background_failed.emit(str(exc))
        except Exception:
            self.background_failed.emit(traceback.format_exc())

    @Slot()
    def clear_background(self) -> None:
        self.background.clear()
        self.background_cleared.emit()
        self.status.emit("Background spectrum cleared.")

    @Slot(float)
    def set_tec_target_c(self, temperature_c: float) -> None:
        try:
            self._require_spectrometer().set_tec_target_c(float(temperature_c))
            self.status.emit(f"TEC target set to {float(temperature_c):.2f} °C.")
        except SpectrometerCommunicationError as exc:
            self._handle_spectrometer_connection_loss(exc)
            self.error.emit(str(exc))
        except Exception:
            self.error.emit(traceback.format_exc())

    @Slot(bool)
    def set_tec_enabled(self, enabled: bool) -> None:
        try:
            self._require_spectrometer().set_tec_enabled(bool(enabled))
            self.status.emit(f"TEC {'enabled' if enabled else 'disabled'}.")
        except SpectrometerCommunicationError as exc:
            self._handle_spectrometer_connection_loss(exc)
            self.error.emit(str(exc))
        except Exception:
            self.error.emit(traceback.format_exc())

    @Slot()
    def query_spectrometer_temperature(self) -> None:
        try:
            temperature = float(self._require_spectrometer().get_ccd_temperature_c())
            self.spectrometer_temperature_ready.emit(temperature)
        except SpectrometerCommunicationError as exc:
            self._handle_spectrometer_connection_loss(exc)
            self.error.emit(str(exc))
        except Exception:
            self.error.emit(traceback.format_exc())

    def _require_spectrometer(self) -> Any:
        if not self.spec_available or self.spec is None:
            raise RuntimeError("Spectrometer is not connected.")
        return self.spec

    def _handle_spectrometer_connection_loss(
        self,
        exc: SpectrometerCommunicationError,
    ) -> None:
        self._close_spectrometer()
        state = InstrumentConnectionState(
            key="spectrometer",
            connected=False,
            emulated=False,
            description="QEPro connection lost.",
            error=str(exc),
        )
        self.spectrometer_connection_changed.emit(state)
        self.status.emit(state.description)

    def _require_power_meter(self) -> PowerMeterAdapter:
        if not self.power_available or self.pm is None:
            raise RuntimeError("Power meter is not connected.")
        return self.pm

    def _emit_spectrometer_info(self) -> None:
        if self.spec is None:
            return

        try:
            self._info = SpectrometerInfo(
                name=str(getattr(self.spec, "name", type(self.spec).__name__)),
                serial_number=str(getattr(self.spec, "serial_number", "")),
                max_intensity=float(getattr(self.spec, "max_intensity", float("nan"))),
                emulated=isinstance(self.spec, EmulatedSpectrometer),
            )
            self.spectrometer_info_ready.emit(self._info)
        except Exception:
            self._info = None

        try:
            self._capabilities = self.spec.capabilities()
        except SpectrometerCommunicationError:
            raise
        except Exception as exc:
            self._capabilities = SpectrometerCapabilities(
                model=type(self.spec).__name__,
                serial_number=str(getattr(self.spec, "serial_number", "")),
                pixels=len(getattr(self.spec, "wavelengths_nm", [])),
                max_intensity=float(getattr(self.spec, "max_intensity", float("nan"))),
                features=["capability_probe_failed"],
                feature_methods={"error": [repr(exc)]},
            )

        self.spectrometer_capabilities_ready.emit(self._capabilities)

    @Slot(object)
    def set_power_monitor_settings(self, settings: PowerMonitorSettings) -> None:
        self.power_monitor_settings = replace(settings)

    def _read_power_validated(self, *, required: bool) -> PowerSnapshot | None:
        if not self.power_available or self.pm is None:
            if required:
                raise RuntimeError("Power meter is not connected.")
            return None

        attempts = max(1, int(self.power_monitor_settings.invalid_power_retries))
        delay_s = max(
            0.0,
            float(self.power_monitor_settings.invalid_power_retry_delay_s),
        )
        last_reason = ""
        last_snapshot: PowerSnapshot | None = None

        for attempt in range(attempts):
            snapshot = self.pm.read_all_power_with_status()
            last_snapshot = snapshot
            valid, reason = power_snapshot_valid(snapshot, self.power_monitor_settings)
            if valid:
                return snapshot

            last_reason = reason
            if attempt < attempts - 1 and delay_s > 0:
                time.sleep(delay_s)

        values = last_snapshot.powers_w if last_snapshot is not None else []
        message = (
            f"Invalid Newport power reading after {attempts} attempt(s): "
            f"{last_reason}; values={values}"
        )

        if required:
            raise RuntimeError(message)

        now = time.monotonic()
        if now - self._last_invalid_power_status_s > 2.0:
            self.status.emit("Ignored invalid Newport power reading: " + last_reason)
            self._last_invalid_power_status_s = now
        return None

    @Slot()
    def poll_power(self) -> None:
        try:
            if (
                not self.power_available
                or not self.power_monitor_settings.live_polling_enabled
            ):
                return

            snapshot = self._read_power_validated(required=False)
            if snapshot is not None:
                self.power_ready.emit(snapshot)
        except Exception:
            self.error.emit(traceback.format_exc())

    @Slot(str)
    def read_power_once(self, tag: str) -> None:
        try:
            snapshot = self._read_power_validated(required=True)
            self.power_read_complete.emit(str(tag), snapshot)
        except Exception:
            message = traceback.format_exc()
            self.power_read_failed.emit(str(tag), message)
            self.error.emit(message)

    @Slot(int)
    def set_power_meter_wavelength_nm(
        self,
        wavelength_nm: int,
    ) -> None:
        if not self.power_available or self.pm is None:
            self.status.emit(
                "Power meter unavailable; "
                "wavelength was not changed."
            )
            return

        try:
            requested = int(round(float(wavelength_nm)))

            self.pm.set_wavelength_for_laser_nm(
                requested
            )

            try:
                actual = int(
                    self.pm.get_wavelength_nm()
                )
            except Exception:
                actual = requested

            self.power_meter_wavelength_ready.emit(
                actual
            )

            self.status.emit(
                f"Newport wavelength set to {actual} nm."
            )

        except Exception:
            self.error.emit(traceback.format_exc())

    @Slot()
    def connect_power_meter(self) -> None:
        self._connect_power_meter_now()


    @Slot()
    def disconnect_power_meter(self) -> None:
        self._close_power_meter()

        self.power_meter_connection_changed.emit(
            InstrumentConnectionState(
                key="power_meter",
                connected=False,
                description="Power meter disconnected.",
            )
        )

        self.status.emit("Power meter disconnected.")

    def _acquire_spectrometer(
        self,
        settings: AcquisitionSettings,
    ) -> SpectralAcquisition:
        spectrometer = self._require_spectrometer()
        return spectrometer.acquire_spectrum(
            integration_ms=int(settings.integration_ms),
            averages=int(settings.averages),
            correct_dark=bool(settings.correct_dark),
            correct_nonlinearity=bool(settings.correct_nonlinearity),
            averaging_mode=str(settings.averaging_mode),
        )

    @Slot(object)
    def acquire(self, settings: AcquisitionSettings) -> None:
        try:
            # Fail before performing a power-meter read when no spectrometer is
            # available. This keeps the two independently connected instruments
            # responsive and avoids a pointless blocking USB request.
            self._require_spectrometer()
            p_before = (
                self._read_power_validated(required=True)
                if self.power_available
                else PowerSnapshot.missing()
            )
            acquisition = self._acquire_spectrometer(settings)
            intensities = np.asarray(acquisition.intensities_counts, dtype=float)

            background_subtracted = False
            background_timestamp_utc = ""
            background_integration_ms = 0
            if settings.subtract_background:
                (
                    intensities,
                    background_subtracted,
                    background_timestamp_utc,
                    background_integration_ms,
                ) = self.background.apply(
                    wavelengths_nm=acquisition.wavelengths_nm,
                    intensities_counts=intensities,
                    integration_ms=int(settings.integration_ms),
                )

            self._snr_acquisition_counter += 1
            snr_metrics = None
            snr_settings = self.snr_settings
            if (
                snr_settings.enabled
                and self._snr_acquisition_counter
                % max(1, int(snr_settings.update_every_n_spectra))
                == 0
            ):
                noise_intervals = [
                    (snr_settings.noise1_start_nm, snr_settings.noise1_stop_nm)
                ]
                if snr_settings.use_noise2:
                    noise_intervals.append(
                        (snr_settings.noise2_start_nm, snr_settings.noise2_stop_nm)
                    )
                snr_metrics = estimate_snr(
                    acquisition.wavelengths_nm,
                    intensities,
                    signal_start_nm=snr_settings.signal_start_nm,
                    signal_stop_nm=snr_settings.signal_stop_nm,
                    noise_intervals_nm=noise_intervals,
                    baseline_order=snr_settings.baseline_order,
                    minimum_noise_pixels=snr_settings.minimum_noise_pixels,
                    peak_percentile=snr_settings.peak_percentile,
                    full_scale_counts=float(
                        getattr(self.spec, "max_intensity", float("nan"))
                    ),
                )

            if settings.boxcar_width > 1:
                intensities = boxcar_smooth(intensities, settings.boxcar_width)

            p_after = (
                self._read_power_validated(required=True)
                if self.power_available
                else PowerSnapshot.missing()
            )

            record = SpectrumRecord(
                timestamp_utc=utc_now_iso(),
                timestamp_s=time.perf_counter(),
                wavelengths_nm=np.asarray(acquisition.wavelengths_nm, dtype=float),
                intensities_counts=intensities,
                p_before=p_before,
                p_after=p_after,
                integration_ms=int(settings.integration_ms),
                averages=int(settings.averages),
                boxcar_width=int(settings.boxcar_width),
                correct_dark=bool(settings.correct_dark),
                correct_nonlinearity=bool(settings.correct_nonlinearity),
                field_value=float(settings.field_value),
                snr=snr_metrics,
                run_identifier=str(settings.run_identifier),
                notes=str(settings.notes),
                signal_max_counts=float(acquisition.signal_max_counts),
                spectrometer_max_intensity=float(
                    getattr(self.spec, "max_intensity", float("nan"))
                ),
                scan_active=bool(settings.scan_active),
                scan_index=int(settings.scan_index),
                scan_count=int(settings.scan_count),
                scan_basis=str(settings.scan_basis),
                scan_spacing=str(settings.scan_spacing),
                laser_port=str(settings.laser_port),
                laser_box_id=str(settings.laser_box_id),
                laser_channel=int(settings.laser_channel),
                laser_wavelength_nm=float(settings.laser_wavelength_nm),
                laser_setpoint_w=float(settings.laser_setpoint_w),
                requested_power_w=float(settings.requested_power_w),
                expected_actual_power_w=float(settings.expected_actual_power_w),
                filter_state=str(settings.filter_state),
                averaging_mode=str(settings.averaging_mode),
                gated=settings.gated,
                device_averaging_used=bool(acquisition.device_averaging_used),
                background_subtracted=bool(background_subtracted),
                background_timestamp_utc=str(background_timestamp_utc),
                background_integration_ms=int(background_integration_ms),
            )
            self.spectrum_ready.emit(record)

        except SpectrometerCommunicationError as exc:
            self._handle_spectrometer_connection_loss(exc)
            self.acquisition_failed.emit(str(exc))

        except Exception:
            self.acquisition_failed.emit(
                traceback.format_exc()
            )

    @Slot()
    def query_spectrometer_capabilities(self) -> None:
        try:
            if self._info is None or self._capabilities is None:
                self._emit_spectrometer_info()
            else:
                self.spectrometer_info_ready.emit(self._info)
                self.spectrometer_capabilities_ready.emit(self._capabilities)
        except SpectrometerCommunicationError as exc:
            self._handle_spectrometer_connection_loss(exc)
            self.error.emit(str(exc))
        except Exception:
            self.error.emit(traceback.format_exc())

    @Slot()
    def shutdown(self) -> None:
        self._close_devices()

    def _close_spectrometer(self) -> None:
        spectrometer = self.spec
        self.spec = None
        self.spec_available = False
        self._capabilities = None
        self._info = None

        if spectrometer is not None:
            close = getattr(spectrometer, "close", None)

            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _close_power_meter(self) -> None:
        meter = self.pm
        self.pm = None
        self.power_available = False

        if meter is not None:
            close = getattr(meter, "close", None)

            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _close_devices(self) -> None:
        self._close_spectrometer()
        self._close_power_meter()
