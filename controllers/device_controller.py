from __future__ import annotations

import traceback
import time

import numpy as np

from PySide6.QtCore import QObject, Signal, Slot

from core.records import PowerSnapshot, SpectrumRecord, SpectrometerInfo, BackgroundSpectrum, SpectrometerCapabilities
from core.settings import DeviceConfig, AcquisitionSettings, PowerMonitorSettings
from core.time_utils import utc_now_iso
from devices.emulated_power_meter import EmulatedPowerMeter
from devices.emulated_spectrometer import EmulatedSpectrometer
from devices.qepro_adapter import QEProSpectrometer
from validation.power_validation import power_snapshot_valid


def missing_power_snapshot() -> PowerSnapshot:
    return PowerSnapshot(
        powers_w=[],
        pm_status=[],
        command_status=-1,
    )


def boxcar_smooth(y: np.ndarray, width: int) -> np.ndarray:
    width = int(width)

    if width <= 1:
        return y

    kernel = np.ones(width, dtype=float) / float(width)
    return np.convolve(y, kernel, mode="same")


class DeviceController(QObject):
    acquisition_failed = Signal(str)
    connected = Signal(str)
    connection_failed = Signal(str)
    spectrum_ready = Signal(object)
    background_ready = Signal(object)
    background_cleared = Signal()
    power_ready = Signal(object)
    power_read_complete = Signal(str, object)
    power_meter_wavelength_ready = Signal(int)
    spectrometer_info_ready = Signal(object)
    spectrometer_capabilities_ready = Signal(object)
    spectrometer_temperature_ready = Signal(float)
    status = Signal(str)
    error = Signal(str)

    def __init__(self, config: DeviceConfig) -> None:
        super().__init__()

        self.config = config

        self.spec = None
        self.pm = None

        self.spec_available = False
        self.power_available = False
        self.connected_ok = False
        self.background_spectrum = None

        self._background_grid_cache_key = None
        self._background_counts_per_s_on_grid = None
        self._last_invalid_power_status_s = 0.0
        self._capabilities = None
        self._info = None

        self.power_monitor_settings = PowerMonitorSettings()


    @Slot()
    def connect_devices(self) -> None:
        messages = []
        errors = []

        self.spec = None
        self.pm = None
        self.spec_available = False
        self.power_available = False
        self.connected_ok = False

        # Spectrometer path.
        try:
            if self.config.emulate:
                self.spec = EmulatedSpectrometer()
                messages.append("Spectrometer: emulator")
            else:
                self.spec = QEProSpectrometer()
                messages.append("Spectrometer: QEPro")

            self.spec_available = True
            self._emit_spectrometer_info()

        except Exception:
            errors.append("Spectrometer connection failed:\n" + traceback.format_exc())

            if self.config.fallback_emulator:
                try:
                    self.spec = EmulatedSpectrometer()
                    self.spec_available = True
                    messages.append("Spectrometer: emulator fallback")
                    self._emit_spectrometer_info()
                except Exception:
                    errors.append("Spectrometer emulator fallback failed:\n" + traceback.format_exc())

        # Power meter path.
        try:
            if self.config.emulate:
                self.pm = EmulatedPowerMeter()
                messages.append("Power meter: emulator")
            else:
                from devices.newport_2936r_dotnet import Newport2936R

                if self.config.newport_dll is None:
                    raise RuntimeError("Real Newport mode requires newport_dll.")

                self.pm = Newport2936R(
                    self.config.newport_dll,
                    channel=self.config.power_channel,
                    units=2,
                )
                messages.append("Power meter: Newport 2936-R")

            self.power_available = True

        except Exception:
            errors.append("Power meter connection failed:\n" + traceback.format_exc())

            if self.config.fallback_emulator:
                try:
                    self.pm = EmulatedPowerMeter()
                    self.power_available = True
                    messages.append("Power meter: emulator fallback")
                except Exception:
                    errors.append("Power meter emulator fallback failed:\n" + traceback.format_exc())

        self.connected_ok = self.spec_available or self.power_available

        if self.connected_ok:
            message = "; ".join(messages)

            if errors:
                message += "\n\nNonfatal connection issue(s):\n" + "\n".join(errors)

            self.connected.emit(message)
        else:
            self.connection_failed.emit(
                "No spectrometer or power meter connected.\n\n" + "\n".join(errors)
            )


    @Slot(object)
    def capture_background(self, settings: AcquisitionSettings) -> None:
        try:
            if not self.spec_available or self.spec is None:
                raise RuntimeError("Spectrometer is not connected.")

            # Background capture should never subtract the previous background.
            settings.subtract_background = False

            result = self.spec.acquire_spectrum(
                integration_ms=int(settings.integration_ms),
                averages=int(settings.averages),
                correct_dark=bool(settings.correct_dark),
                correct_nonlinearity=bool(settings.correct_nonlinearity),
                averaging_mode=str(settings.averaging_mode),
            )

            if len(result) == 4:
                wavelengths_nm, intensities, signal_max, device_averaging_used = result
            elif len(result) == 3:
                wavelengths_nm, intensities, signal_max = result
                device_averaging_used = False
            else:
                wavelengths_nm, intensities = result
                device_averaging_used = False # noqa

            wavelengths_nm = np.asarray(wavelengths_nm, dtype=float)
            intensities = np.asarray(intensities, dtype=float)

            integration_s = max(float(settings.integration_ms) * 1.0e-3, 1.0e-12)

            self.background_spectrum = BackgroundSpectrum(
                timestamp_utc=utc_now_iso(),
                wavelengths_nm=wavelengths_nm,
                counts_per_s=intensities / integration_s,
                integration_ms=int(settings.integration_ms),
                averages=int(settings.averages),
                correct_dark=bool(settings.correct_dark),
                correct_nonlinearity=bool(settings.correct_nonlinearity),
                averaging_mode=str(settings.averaging_mode),
            )

            self.background_ready.emit(self.background_spectrum)
            self.status.emit("Background spectrum captured.")

        except Exception:
            self.error.emit(traceback.format_exc())


    @Slot()
    def clear_background(self) -> None:
        self.background_spectrum = None
        self.background_cleared.emit()
        self.status.emit("Background spectrum cleared.")


    @Slot(float)
    def set_tec_target_c(self, temperature_c: float) -> None:
        try:
            if not self.spec_available or self.spec is None:
                raise RuntimeError("Spectrometer is not connected.")

            self.spec.set_tec_target_c(float(temperature_c))
            self.status.emit(f"TEC target set to {float(temperature_c):.2f} °C.")

        except Exception:
            self.error.emit(traceback.format_exc())


    @Slot(bool)
    def set_tec_enabled(self, enabled: bool) -> None:
        try:
            if not self.spec_available or self.spec is None:
                raise RuntimeError("Spectrometer is not connected.")

            self.spec.set_tec_enabled(bool(enabled))
            state = "enabled" if enabled else "disabled"
            self.status.emit(f"TEC {state}.")

        except Exception:
            self.error.emit(traceback.format_exc())


    @Slot()
    def query_spectrometer_temperature(self) -> None:
        try:
            if not self.spec_available or self.spec is None:
                raise RuntimeError("Spectrometer is not connected.")

            temp = float(self.spec.get_ccd_temperature_c())
            self.spectrometer_temperature_ready.emit(temp)

        except Exception:
            self.error.emit(traceback.format_exc())


    def _prepare_background_for_grid(self, wavelengths_nm: np.ndarray) -> np.ndarray:
        bg = self.background_spectrum

        if bg is None:
            raise RuntimeError("No background spectrum")

        current = np.asarray(wavelengths_nm, dtype=float)

        cache_key = (
            current.shape,
            float(current[0]) if current.size else float("nan"),
            float(current[-1]) if current.size else float("nan"),
        )

        old_key = getattr(self, "_background_grid_cache_key", None)

        if old_key == cache_key:
            cached = getattr(self, "_background_counts_per_s_on_grid", None)
            if cached is not None:
                return cached

        bg_wl = np.asarray(bg.wavelengths_nm, dtype=float)
        bg_cps = np.asarray(bg.counts_per_s, dtype=float)

        if current.shape == bg_wl.shape and np.allclose(current, bg_wl, rtol=0.0, atol=1e-9):
            out = bg_cps
        else:
            out = np.interp(current, bg_wl, bg_cps)

        self._background_grid_cache_key = cache_key
        self._background_counts_per_s_on_grid = out

        return out


    def _apply_background(
        self,
        *,
        wavelengths_nm: np.ndarray,
        intensities_counts: np.ndarray,
        integration_ms: int,
    ) -> tuple[np.ndarray, bool, str, int]:
        if self.background_spectrum is None:
            return intensities_counts, False, "", 0

        bg_cps = self._prepare_background_for_grid(wavelengths_nm)
        integration_s = max(float(integration_ms) * 1.0e-3, 1.0e-12)

        corrected = np.asarray(intensities_counts, dtype=float) - bg_cps * integration_s

        return (
            corrected,
            True,
            self.background_spectrum.timestamp_utc,
            int(self.background_spectrum.integration_ms),
        )


    def _emit_spectrometer_info(self) -> None:
        if self.spec is None:
            return

        try:
            info = SpectrometerInfo(
                name=str(getattr(self.spec, "name", type(self.spec).__name__)),
                serial_number=str(getattr(self.spec, "serial_number", "")),
                max_intensity=float(getattr(self.spec, "max_intensity", float("nan"))),
                emulated=type(self.spec).__name__.lower().startswith("emulated"),
            )
            self._info = info
            self.spectrometer_info_ready.emit(info)
        except Exception:
            pass

        try:
            caps = self.spec.capabilities()
        except Exception as exc:
            caps = SpectrometerCapabilities(
                model=str(type(self.spec).__name__),
                serial_number=str(getattr(self.spec, "serial_number", "")),
                pixels=len(getattr(self.spec, "wavelengths_nm", [])),
                max_intensity=float(getattr(self.spec, "max_intensity", float("nan"))),
                features=["capability_probe_failed"],
                feature_methods={"error": [repr(exc)]},
            )

        self._capabilities = caps
        self.spectrometer_capabilities_ready.emit(caps)


    @Slot(object)
    def set_power_monitor_settings(self, settings: PowerMonitorSettings) -> None:
        self.power_monitor_settings = settings

    def _read_power_validated(self, *, required: bool) -> PowerSnapshot | None:
        if not self.power_available or self.pm is None:
            if required:
                raise RuntimeError("Power meter is not connected.")
            return None

        attempts = max(1, int(self.power_monitor_settings.invalid_power_retries))
        delay_s = max(0.0, float(self.power_monitor_settings.invalid_power_retry_delay_s))

        last_reason = ""
        last_snapshot = None

        for attempt in range(attempts):
            snapshot = self.pm.read_all_power_with_status()
            last_snapshot = snapshot

            ok, reason = power_snapshot_valid(snapshot, self.power_monitor_settings)

            if ok:
                return snapshot

            last_reason = reason

            if attempt < attempts - 1 and delay_s > 0:
                time.sleep(delay_s)

        values = last_snapshot.powers_w if last_snapshot is not None else []

        now = time.monotonic()
        if now - self._last_invalid_power_status_s > 2.0:
            message = (
                f"Invalid Newport power reading after {attempts} attempt(s): "
                f"{last_reason}; values={values}"
            )
    
            if required:
                raise RuntimeError(message)
    
            self.status.emit("Ignored invalid Newport power reading: " + last_reason)
            self._last_invalid_power_status_s = now

        return None


    @Slot()
    def poll_power(self) -> None:
        try:
            if not self.power_available or self.pm is None:
                return

            if (
                not self.power_monitor_settings.polling_enabled
                or self.power_monitor_settings.mode != "live"
            ):
                return

            snapshot = self._read_power_validated(required=False)

            if snapshot is None:
                return

            self.power_ready.emit(snapshot)

        except Exception:
            self.error.emit(traceback.format_exc())


    @Slot(str)
    def read_power_once(self, tag: str) -> None:
        try:
            snapshot = self._read_power_validated(required=True)
            self.power_read_complete.emit(str(tag), snapshot)

        except Exception:
            self.error.emit(traceback.format_exc())


    @Slot(int)
    def set_power_meter_wavelength_nm(self, wavelength_nm: int) -> None:
        try:
            if not self.power_available or self.pm is None:
                raise RuntimeError("Power meter is not connected.")

            wl = int(round(float(wavelength_nm)))
            self.pm.set_wavelength_for_laser_nm(wl)

            try:
                actual_wl = int(self.pm.get_wavelength_nm())
            except Exception:
                actual_wl = wl

            self.power_meter_wavelength_ready.emit(actual_wl)
            self.status.emit(f"Newport wavelength set to {actual_wl} nm.")

        except Exception:
            self.error.emit(traceback.format_exc())


    @Slot(object)
    def acquire(self, settings: AcquisitionSettings) -> None:
        try:
            if not self.spec_available or self.spec is None:
                raise RuntimeError("Spectrometer is not connected.")

            if self.power_available and self.pm is not None:
                p_before = self._read_power_validated(required=True)
            else:
                p_before = missing_power_snapshot()

            result = self.spec.acquire_spectrum(
                integration_ms=int(settings.integration_ms),
                averages=int(settings.averages),
                correct_dark=bool(settings.correct_dark),
                correct_nonlinearity=bool(settings.correct_nonlinearity),
                averaging_mode=str(settings.averaging_mode),
            )

            if len(result) == 4:
                wavelengths_nm, intensities, signal_max_counts, device_averaging_used = result
            elif len(result) == 3:
                wavelengths_nm, intensities, signal_max_counts = result
                device_averaging_used = False
            else:
                wavelengths_nm, intensities = result
                signal_max_counts = float(np.nanmax(intensities))
                device_averaging_used = False

            wavelengths_nm = np.asarray(wavelengths_nm, dtype=float)
            intensities = np.asarray(intensities, dtype=float)

            background_subtracted = False
            background_timestamp_utc = ""
            background_integration_ms = 0

            if bool(settings.subtract_background):
                (
                    intensities,
                    background_subtracted,
                    background_timestamp_utc,
                    background_integration_ms,
                ) = self._apply_background(
                    wavelengths_nm=wavelengths_nm,
                    intensities_counts=intensities,
                    integration_ms=int(settings.integration_ms),
                )

            if int(settings.boxcar_width) > 1:
                intensities = boxcar_smooth(intensities, int(settings.boxcar_width))

            if self.power_available and self.pm is not None:
                p_after = self._read_power_validated(required=True)
            else:
                p_after = missing_power_snapshot()

            record = SpectrumRecord(
                timestamp_utc=utc_now_iso(),
                timestamp_s=time.perf_counter(),
                wavelengths_nm=np.asarray(wavelengths_nm, dtype=float),
                intensities_counts=intensities,
                p_before=p_before,
                p_after=p_after,
                integration_ms=int(settings.integration_ms),
                averages=int(settings.averages),
                boxcar_width=int(settings.boxcar_width),
                correct_dark=bool(settings.correct_dark),
                correct_nonlinearity=bool(settings.correct_nonlinearity),
                field_value=float(settings.field_value),
                run_identifier=str(settings.run_identifier),
                notes=str(settings.notes),
                signal_max_counts=float(signal_max_counts),
                spectrometer_max_intensity=float(getattr(self.spec, "max_intensity", float("nan"))),

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
                device_averaging_used=bool(device_averaging_used),

                background_subtracted=bool(background_subtracted),
                background_timestamp_utc=str(background_timestamp_utc),
                background_integration_ms=int(background_integration_ms),
            )

            self.spectrum_ready.emit(record)

        except Exception:
            self.acquisition_failed.emit(traceback.format_exc())


    @Slot()
    def query_spectrometer_capabilities(self) -> None:
        try:
            if self._info is None or self._capabilities is None:
                self._emit_spectrometer_info()
            else:
                self.spectrometer_info_ready.emit(self._info)
                self.spectrometer_capabilities_ready.emit(self._capabilities)
        except Exception:
            self.error.emit(traceback.format_exc())


    @Slot()
    def shutdown(self) -> None:
        for obj in [self.spec, self.pm]:
            if obj is None:
                continue

            close = getattr(obj, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        self.spec = None
        self.pm = None
        self.spec_available = False
        self.power_available = False
        self.connected_ok = False
