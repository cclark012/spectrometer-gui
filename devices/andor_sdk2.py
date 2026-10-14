from __future__ import annotations

import ctypes
import math
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from core.records import AcquisitionTiming

DRV_SUCCESS = 20002
DRV_NO_NEW_DATA = 20024
DRV_INVALID_MODE = 20078
DRV_TEMPERATURE_CODES = {20034, 20035, 20036, 20037, 20038, 20039, 20040}
DRV_RETURN_NAMES = {
    20002: "DRV_SUCCESS",
    20003: "DRV_VXDNOTINSTALLED",
    20006: "DRV_ERROR_FILELOAD",
    20010: "DRV_ERROR_PAGELOCK",
    20013: "DRV_ERROR_ACK",
    20024: "DRV_NO_NEW_DATA",
    20066: "DRV_P1INVALID",
    20067: "DRV_P2INVALID",
    20068: "DRV_P3INVALID",
    20069: "DRV_P4INVALID",
    20070: "DRV_INIERROR",
    20071: "DRV_COFERROR",
    20072: "DRV_ACQUIRING",
    20073: "DRV_IDLE",
    20075: "DRV_NOT_INITIALIZED",
    20078: "DRV_INVALID_MODE",
    20089: "DRV_USBERROR",
    20093: "DRV_USB_INTERRUPT_ENDPOINT_ERROR",
    20099: "DRV_BINNING_ERROR",
    20100: "DRV_INVALID_AMPLIFIER",
    20990: "DRV_ERROR_NOCAMERA",
    20991: "DRV_NOT_SUPPORTED",
    20992: "DRV_NOT_AVAILABLE",
}


class AndorSDK2Error(RuntimeError):
    pass


class AndorCameraNotFoundError(AndorSDK2Error):
    pass


@dataclass(frozen=True, slots=True)
class AndorCameraSettings:
    ad_channel: int = 0
    output_amplifier: int = 0
    horizontal_speed_index: int = 0
    vertical_speed_index: int = 0
    preamp_gain_index: int = 0
    horizontal_binning: int = 1


@dataclass(frozen=True, slots=True)
class AndorAcquisitionProfile:
    """SDK-reported timing for the active camera configuration."""

    requested_exposure_ms: float = float("nan")
    applied_exposure_ms: float = float("nan")
    accumulation_cycle_ms: float = float("nan")
    kinetic_cycle_ms: float = float("nan")
    readout_ms: float = float("nan")
    keep_clean_ms: float = float("nan")


@dataclass(frozen=True, slots=True)
class AndorCameraCapabilities:
    model: str
    serial_number: str
    detector_pixels_x: int
    detector_pixels_y: int
    pixel_width_um: float
    pixel_height_um: float
    ad_channels: tuple[int, ...]
    output_amplifiers: tuple[int, ...]
    horizontal_speeds_mhz: dict[str, tuple[float, ...]]
    vertical_speeds_us: tuple[float, ...]
    preamp_gains: tuple[float, ...]
    preamp_gain_indices: dict[str, tuple[int, ...]]
    bit_depth: int
    temperature_min_c: int
    temperature_max_c: int
    # Full-vertical-binning spectroscopy on the lab DU401 accepts 1 only;
    # SDK2 returned DRV_NOT_SUPPORTED for 4 even though the detector width is
    # divisible by 4. Do not infer writable binning values from pixel geometry.
    horizontal_binnings: tuple[int, ...] = (1,)
    cooler_enabled: bool | None = None
    minimum_exposure_ms: float = 1.0
    maximum_exposure_ms: float = 3_600_000.0
    minimum_frame_period_ms: float = float("nan")
    readout_time_ms: float = float("nan")
    keep_clean_time_ms: float = float("nan")

    def as_control_schema(self) -> dict[str, Any]:
        return asdict(self)


class AndorSDK2Camera:
    """SDK2 wrapper for full-vertical-binning spectroscopy and streaming."""

    # The lab DU401_BVF is enumerated by the legacy 64-bit SDK2 runtime but not
    # by atmcd64d.dll.  Keep the proven runtime first and allow an exact DLL to
    # be configured for other installations.
    DLL_NAMES = (
        "atmcd64d_legacy.dll",
        "atmcd64d.dll",
        "atmcd32d_legacy.dll",
        "atmcd32d.dll",
    )

    def __init__(
        self,
        solis_dir: str | Path,
        *,
        camera_index: int = 0,
        settings: AndorCameraSettings | None = None,
        camera_dll: str | Path | None = None,
    ) -> None:
        self.solis_dir = Path(solis_dir)
        self.camera_index = int(camera_index)
        explicit_settings = settings is not None
        self.settings = settings or AndorCameraSettings()
        self.requested_camera_dll = Path(camera_dll) if camera_dll else None
        self.dll_path: Path | None = None
        self.last_timing: AcquisitionTiming | None = None
        self.last_acquisition_profile = AndorAcquisitionProfile()
        # Diagnostic-only wall timings. These are deliberately not part of
        # spectrum metadata; troubleshooting/andor_timing_probe.py reports
        # them when separating SDK setup, wait, and data-transfer overhead.
        self.last_stage_timing_ms: dict[str, Any] = {}
        self.temperature_target_c: float | None = None
        self._prepared_signature: tuple[AndorCameraSettings, float] | None = None
        self._continuous_signature: tuple[AndorCameraSettings, float, str] | None = None
        self._continuous_started_s = float("nan")
        self._continuous_next_driver_index: int | None = None
        self._initialized = False
        self._dll_directory_handle = None
        self._dll = self._load_dll()

        count = ctypes.c_long()
        self._call(
            "GetAvailableCameras",
            ctypes.byref(count),
            argtypes=[ctypes.POINTER(ctypes.c_long)],
        )
        if count.value <= 0:
            self.close()
            raise AndorCameraNotFoundError(
                "Andor SDK2 reported zero available cameras. Power the iDus, close "
                "Solis, verify its USB connection/driver, and retry. Kymera-only "
                "pixel values of 0 are not usable detector geometry."
            )
        if not 0 <= self.camera_index < int(count.value):
            self.close()
            raise AndorCameraNotFoundError(
                f"Camera index {self.camera_index} is outside 0..{int(count.value) - 1}."
            )

        handle = ctypes.c_long()
        self._call(
            "GetCameraHandle",
            ctypes.c_long(self.camera_index),
            ctypes.byref(handle),
            argtypes=[ctypes.c_long, ctypes.POINTER(ctypes.c_long)],
        )
        self._call(
            "SetCurrentCamera",
            ctypes.c_long(handle.value),
            argtypes=[ctypes.c_long],
        )
        self._call(
            "Initialize",
            os.fsencode(str(self.solis_dir)),
            argtypes=[ctypes.c_char_p],
        )
        self._initialized = True
        try:
            self.capabilities = self._read_capabilities()
            if not explicit_settings and self.capabilities.vertical_speeds_us:
                # The DU401 specification identifies approximately 16 us/pixel
                # as the optimum charge-transfer-efficiency setting. SDK speed
                # indices are only an ordered capability list; index 0 is not a
                # meaningful factory default.
                preferred_index = min(
                    range(len(self.capabilities.vertical_speeds_us)),
                    key=lambda index: abs(
                        float(self.capabilities.vertical_speeds_us[index]) - 16.0
                    ),
                )
                self.settings = replace(
                    self.settings,
                    vertical_speed_index=int(preferred_index),
                )
            self.apply_settings(self.settings)
        except Exception:
            self.close()
            raise

    def _load_dll(self):
        if not hasattr(ctypes, "WinDLL"):
            raise AndorSDK2Error(
                "The Andor SDK2 native adapter is available only on Windows."
            )
        requested = self.requested_camera_dll
        if requested is not None:
            dll_path = requested if requested.is_absolute() else self.solis_dir / requested
            if not dll_path.exists():
                raise FileNotFoundError(
                    f"Configured Andor SDK2 camera DLL was not found: {dll_path}"
                )
        else:
            dll_path = next(
                (
                    self.solis_dir / name
                    for name in self.DLL_NAMES
                    if (self.solis_dir / name).exists()
                ),
                None,
            )
        if dll_path is None:
            raise FileNotFoundError(f"No Andor SDK2 camera DLL found in {self.solis_dir}")
        if hasattr(os, "add_dll_directory"):
            self._dll_directory_handle = os.add_dll_directory(str(self.solis_dir))
        self.dll_path = dll_path.resolve()
        return ctypes.WinDLL(str(self.dll_path))

    def _function(self, name: str, argtypes: list[Any]):
        function = getattr(self._dll, name, None)
        if function is None:
            raise AndorSDK2Error(f"Andor SDK2 does not export {name}.")
        function.argtypes = argtypes
        function.restype = ctypes.c_uint
        return function

    def _call(
        self,
        name: str,
        *args: Any,
        argtypes: list[Any],
        accepted: set[int] | None = None,
    ) -> int:
        code = int(self._function(name, argtypes)(*args))
        accepted_codes = {DRV_SUCCESS} if accepted is None else set(accepted)
        if code not in accepted_codes:
            code_name = DRV_RETURN_NAMES.get(code, "UNKNOWN_SDK2_STATUS")
            raise AndorSDK2Error(
                f"{name} failed with SDK2 return code {code} ({code_name})."
            )
        return code

    def _optional_values(self, count_name: str, value_name: str) -> tuple[float, ...]:
        count = ctypes.c_int()
        try:
            self._call(count_name, ctypes.byref(count), argtypes=[ctypes.POINTER(ctypes.c_int)])
        except AndorSDK2Error:
            return ()
        values: list[float] = []
        for index in range(max(0, int(count.value))):
            value = ctypes.c_float()
            try:
                self._call(
                    value_name,
                    ctypes.c_int(index),
                    ctypes.byref(value),
                    argtypes=[ctypes.c_int, ctypes.POINTER(ctypes.c_float)],
                )
            except AndorSDK2Error:
                continue
            values.append(float(value.value))
        return tuple(values)

    def _optional_float(self, name: str) -> float:
        value = ctypes.c_float()
        try:
            self._call(
                name,
                ctypes.byref(value),
                argtypes=[ctypes.POINTER(ctypes.c_float)],
            )
        except AndorSDK2Error:
            return float("nan")
        result = float(value.value)
        return result if math.isfinite(result) else float("nan")

    def _read_acquisition_profile(
        self,
        *,
        requested_exposure_ms: float,
    ) -> AndorAcquisitionProfile:
        exposure = ctypes.c_float()
        accumulation = ctypes.c_float()
        kinetic = ctypes.c_float()
        self._call(
            "GetAcquisitionTimings",
            ctypes.byref(exposure),
            ctypes.byref(accumulation),
            ctypes.byref(kinetic),
            argtypes=[
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
            ],
        )
        return AndorAcquisitionProfile(
            requested_exposure_ms=float(requested_exposure_ms),
            applied_exposure_ms=1000.0 * float(exposure.value),
            accumulation_cycle_ms=1000.0 * float(accumulation.value),
            kinetic_cycle_ms=1000.0 * float(kinetic.value),
            readout_ms=1000.0 * self._optional_float("GetReadOutTime"),
            keep_clean_ms=1000.0 * self._optional_float("GetKeepCleanTime"),
        )

    def _refresh_exposure_limits(self) -> None:
        """Ask SDK2 for the applied minimum under the selected readout settings."""

        probe_ms = 0.001
        self._call(
            "SetExposureTime",
            ctypes.c_float(probe_ms * 1.0e-3),
            argtypes=[ctypes.c_float],
        )
        profile = self._read_acquisition_profile(
            requested_exposure_ms=probe_ms,
        )
        minimum_ms = float(profile.applied_exposure_ms)
        if not math.isfinite(minimum_ms) or minimum_ms <= 0.0:
            minimum_ms = 1.0
        maximum_seconds = self._optional_float("GetMaximumExposure")
        maximum_ms = 1000.0 * maximum_seconds
        if not math.isfinite(maximum_ms) or maximum_ms < minimum_ms:
            maximum_ms = 3_600_000.0
        self.capabilities = replace(
            self.capabilities,
            minimum_exposure_ms=minimum_ms,
            maximum_exposure_ms=maximum_ms,
            minimum_frame_period_ms=float(profile.kinetic_cycle_ms),
            readout_time_ms=float(profile.readout_ms),
            keep_clean_time_ms=float(profile.keep_clean_ms),
        )

    def _read_capabilities(self) -> AndorCameraCapabilities:
        model_buffer = ctypes.create_string_buffer(256)
        self._call("GetHeadModel", model_buffer, argtypes=[ctypes.c_char_p])
        serial = ctypes.c_int()
        self._call(
            "GetCameraSerialNumber",
            ctypes.byref(serial),
            argtypes=[ctypes.POINTER(ctypes.c_int)],
        )
        pixels_x = ctypes.c_int()
        pixels_y = ctypes.c_int()
        self._call(
            "GetDetector",
            ctypes.byref(pixels_x),
            ctypes.byref(pixels_y),
            argtypes=[ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)],
        )
        pixel_x = ctypes.c_float()
        pixel_y = ctypes.c_float()
        self._call(
            "GetPixelSize",
            ctypes.byref(pixel_x),
            ctypes.byref(pixel_y),
            argtypes=[ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)],
        )
        if pixels_x.value <= 0 or pixel_x.value <= 0:
            raise AndorSDK2Error(
                "SDK2 returned invalid detector geometry: "
                f"pixels={pixels_x.value}×{pixels_y.value}, "
                f"pitch={pixel_x.value}×{pixel_y.value} µm."
            )

        ad_count = ctypes.c_int()
        self._call(
            "GetNumberADChannels",
            ctypes.byref(ad_count),
            argtypes=[ctypes.POINTER(ctypes.c_int)],
        )
        amp_count = ctypes.c_int()
        self._call(
            "GetNumberAmp",
            ctypes.byref(amp_count),
            argtypes=[ctypes.POINTER(ctypes.c_int)],
        )
        if ad_count.value <= 0 or amp_count.value <= 0:
            raise AndorSDK2Error(
                "SDK2 reported no usable A/D channels or output amplifiers: "
                f"A/D={ad_count.value}, amplifiers={amp_count.value}."
            )

        horizontal: dict[str, tuple[float, ...]] = {}
        for ad_channel in range(max(0, int(ad_count.value))):
            for amplifier in range(max(0, int(amp_count.value))):
                count = ctypes.c_int()
                try:
                    self._call(
                        "GetNumberHSSpeeds",
                        ctypes.c_int(ad_channel),
                        ctypes.c_int(amplifier),
                        ctypes.byref(count),
                        argtypes=[
                            ctypes.c_int,
                            ctypes.c_int,
                            ctypes.POINTER(ctypes.c_int),
                        ],
                    )
                except AndorSDK2Error:
                    continue
                speeds: list[float] = []
                for index in range(max(0, int(count.value))):
                    speed = ctypes.c_float()
                    self._call(
                        "GetHSSpeed",
                        ctypes.c_int(ad_channel),
                        ctypes.c_int(amplifier),
                        ctypes.c_int(index),
                        ctypes.byref(speed),
                        argtypes=[
                            ctypes.c_int,
                            ctypes.c_int,
                            ctypes.c_int,
                            ctypes.POINTER(ctypes.c_float),
                        ],
                    )
                    speeds.append(float(speed.value))
                horizontal[f"{ad_channel}:{amplifier}"] = tuple(speeds)

        bit_depth = ctypes.c_int()
        self._call(
            "GetBitDepth",
            ctypes.c_int(self.settings.ad_channel),
            ctypes.byref(bit_depth),
            argtypes=[ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
        )
        temperature_min = ctypes.c_int()
        temperature_max = ctypes.c_int()
        self._call(
            "GetTemperatureRange",
            ctypes.byref(temperature_min),
            ctypes.byref(temperature_max),
            argtypes=[
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
            ],
        )
        preamp_gains = self._optional_values(
            "GetNumberPreAmpGains",
            "GetPreAmpGain",
        )
        preamp_gain_indices: dict[str, tuple[int, ...]] = {}
        for horizontal_key, speeds in horizontal.items():
            ad_channel, amplifier = (
                int(value) for value in horizontal_key.split(":", maxsplit=1)
            )
            for speed_index in range(len(speeds)):
                availability_key = f"{horizontal_key}:{speed_index}"
                available: list[int] = []
                availability_supported = True
                for gain_index in range(len(preamp_gains)):
                    status = ctypes.c_int()
                    try:
                        self._call(
                            "IsPreAmpGainAvailable",
                            ctypes.c_int(ad_channel),
                            ctypes.c_int(amplifier),
                            ctypes.c_int(speed_index),
                            ctypes.c_int(gain_index),
                            ctypes.byref(status),
                            argtypes=[
                                ctypes.c_int,
                                ctypes.c_int,
                                ctypes.c_int,
                                ctypes.c_int,
                                ctypes.POINTER(ctypes.c_int),
                            ],
                        )
                    except AndorSDK2Error:
                        availability_supported = False
                        break
                    if status.value:
                        available.append(gain_index)
                preamp_gain_indices[availability_key] = (
                    tuple(available)
                    if availability_supported
                    else tuple(range(len(preamp_gains)))
                )

        cooler_status = ctypes.c_int()
        try:
            self._call(
                "IsCoolerOn",
                ctypes.byref(cooler_status),
                argtypes=[ctypes.POINTER(ctypes.c_int)],
            )
            cooler_enabled: bool | None = bool(cooler_status.value)
        except AndorSDK2Error:
            cooler_enabled = None

        return AndorCameraCapabilities(
            model=bytes(model_buffer.value).decode("ascii", errors="replace").strip(),
            serial_number=str(int(serial.value)),
            detector_pixels_x=int(pixels_x.value),
            detector_pixels_y=int(pixels_y.value),
            pixel_width_um=float(pixel_x.value),
            pixel_height_um=float(pixel_y.value),
            ad_channels=tuple(range(max(0, int(ad_count.value)))),
            output_amplifiers=tuple(range(max(0, int(amp_count.value)))),
            horizontal_speeds_mhz=horizontal,
            vertical_speeds_us=self._optional_values("GetNumberVSSpeeds", "GetVSSpeed"),
            preamp_gains=preamp_gains,
            preamp_gain_indices=preamp_gain_indices,
            bit_depth=max(1, int(bit_depth.value)),
            temperature_min_c=int(temperature_min.value),
            temperature_max_c=int(temperature_max.value),
            horizontal_binnings=(1,),
            cooler_enabled=cooler_enabled,
        )

    @property
    def output_pixel_count(self) -> int:
        binning = max(1, int(self.settings.horizontal_binning))
        return int(self.capabilities.detector_pixels_x) // binning

    @property
    def effective_pixel_width_um(self) -> float:
        return float(self.capabilities.pixel_width_um) * max(
            1, int(self.settings.horizontal_binning)
        )

    @property
    def max_intensity(self) -> float:
        return float((1 << int(self.capabilities.bit_depth)) - 1)

    def apply_settings(self, settings: AndorCameraSettings) -> None:
        settings = replace(settings)
        if settings.ad_channel not in self.capabilities.ad_channels:
            raise ValueError(f"Unknown Andor A/D channel {settings.ad_channel}.")
        if settings.output_amplifier not in self.capabilities.output_amplifiers:
            raise ValueError(
                f"Unknown Andor output amplifier {settings.output_amplifier}."
            )
        if settings.horizontal_binning < 1:
            raise ValueError("Andor horizontal binning must be at least 1.")
        valid_binnings = tuple(
            int(value) for value in self.capabilities.horizontal_binnings
        )
        if valid_binnings and settings.horizontal_binning not in valid_binnings:
            raise ValueError(
                "Andor horizontal binning is unavailable in this camera's "
                f"full-vertical-binning mode; valid values are {valid_binnings}."
            )
        if self.capabilities.detector_pixels_x % settings.horizontal_binning:
            raise ValueError("Horizontal binning must divide the detector pixel count.")
        horizontal_key = f"{settings.ad_channel}:{settings.output_amplifier}"
        horizontal_speeds = self.capabilities.horizontal_speeds_mhz.get(
            horizontal_key,
            (),
        )
        if horizontal_speeds and not 0 <= settings.horizontal_speed_index < len(
            horizontal_speeds
        ):
            raise ValueError(
                "Andor horizontal-speed index is invalid for the selected "
                "A/D channel and output amplifier."
            )
        if self.capabilities.vertical_speeds_us and not (
            0 <= settings.vertical_speed_index < len(self.capabilities.vertical_speeds_us)
        ):
            raise ValueError("Unknown Andor vertical-speed index.")
        if self.capabilities.preamp_gains and not (
            0 <= settings.preamp_gain_index < len(self.capabilities.preamp_gains)
        ):
            raise ValueError("Unknown Andor preamp-gain index.")
        gain_key = f"{horizontal_key}:{settings.horizontal_speed_index}"
        if (
            gain_key in self.capabilities.preamp_gain_indices
            and settings.preamp_gain_index
            not in self.capabilities.preamp_gain_indices[gain_key]
        ):
            raise ValueError(
                "The selected Andor preamp gain is unavailable for this "
                "A/D, amplifier, and horizontal-speed combination."
            )

        # SDK2 rejects readout changes while Run-Till-Abort is active.
        self.stop_continuous_acquisition()
        calls: list[tuple[str, tuple[Any, ...], list[Any]]] = [
            ("SetReadMode", (ctypes.c_int(0),), [ctypes.c_int]),
            ("SetAcquisitionMode", (ctypes.c_int(1),), [ctypes.c_int]),
            ("SetTriggerMode", (ctypes.c_int(0),), [ctypes.c_int]),
            ("SetADChannel", (ctypes.c_int(settings.ad_channel),), [ctypes.c_int]),
            (
                "SetOutputAmplifier",
                (ctypes.c_int(settings.output_amplifier),),
                [ctypes.c_int],
            ),
            (
                "SetFVBHBin",
                (ctypes.c_int(settings.horizontal_binning),),
                [ctypes.c_int],
            ),
        ]
        if horizontal_speeds:
            calls.append(
                (
                    "SetHSSpeed",
                    (
                        ctypes.c_int(settings.output_amplifier),
                        ctypes.c_int(settings.horizontal_speed_index),
                    ),
                    [ctypes.c_int, ctypes.c_int],
                )
            )
        if self.capabilities.vertical_speeds_us:
            calls.append(
                (
                    "SetVSSpeed",
                    (ctypes.c_int(settings.vertical_speed_index),),
                    [ctypes.c_int],
                )
            )
        if self.capabilities.preamp_gains:
            calls.append(
                (
                    "SetPreAmpGain",
                    (ctypes.c_int(settings.preamp_gain_index),),
                    [ctypes.c_int],
                )
            )
        # Any attempted control change invalidates the existing prepared state,
        # including a partially failed native update.
        self._prepared_signature = None
        for name, args, argtypes in calls:
            self._call(name, *args, argtypes=argtypes)
        self.settings = settings
        bit_depth = ctypes.c_int()
        self._call(
            "GetBitDepth",
            ctypes.c_int(settings.ad_channel),
            ctypes.byref(bit_depth),
            argtypes=[ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
        )
        self.capabilities = replace(
            self.capabilities,
            bit_depth=max(1, int(bit_depth.value)),
        )
        self._refresh_exposure_limits()

    @staticmethod
    def _timeout_ms(exposure_ms: float, profile: AndorAcquisitionProfile) -> int:
        finite_periods = [
            value
            for value in (
                exposure_ms,
                profile.applied_exposure_ms,
                profile.kinetic_cycle_ms,
                profile.readout_ms + profile.keep_clean_ms,
            )
            if math.isfinite(value) and value > 0.0
        ]
        expected_period_ms = max(finite_periods, default=exposure_ms)
        return min(
            2_147_000_000,
            max(10_000, int(math.ceil(2.0 * expected_period_ms + 10_000.0))),
        )

    def _number_new_images(self) -> tuple[int, int] | None:
        """Return the unread SDK2 image-index range, or ``None`` when empty."""

        if getattr(self._dll, "GetNumberNewImages", None) is None:
            return None
        first = ctypes.c_long()
        last = ctypes.c_long()
        code = self._call(
            "GetNumberNewImages",
            ctypes.byref(first),
            ctypes.byref(last),
            argtypes=[
                ctypes.POINTER(ctypes.c_long),
                ctypes.POINTER(ctypes.c_long),
            ],
            accepted={DRV_SUCCESS, DRV_NO_NEW_DATA},
        )
        if code == DRV_NO_NEW_DATA or int(last.value) < int(first.value):
            return None
        return int(first.value), int(last.value)

    def _read_oldest_image(self, buffer, count: int) -> None:
        self._call(
            "GetOldestImage",
            buffer,
            ctypes.c_ulong(count),
            argtypes=[ctypes.POINTER(ctypes.c_int32), ctypes.c_ulong],
        )

    def _drain_pending_continuous_images(self, buffer, count: int) -> int:
        """Discard only frames that were complete when this request arrived."""

        available = self._number_new_images()
        if available is None:
            return 0
        first, last = available
        pending = last - first + 1
        # A corrupt/native index range must never create an unbounded drain.
        if pending < 0 or pending > 1_000_000:
            raise AndorSDK2Error(
                f"SDK2 returned an invalid unread image range {first}..{last}."
            )
        for index in range(pending):
            self._read_oldest_image(buffer, count)
            self._continuous_next_driver_index = first + index + 1
        return pending

    def _wait_for_continuous_image(
        self,
        *,
        timeout_ms: int,
    ) -> tuple[tuple[int, int], int]:
        """Wait until SDK2 reports an unread image, ignoring stale events.

        SDK2 acquisition events and circular-buffer reads are separate state.
        The lab driver returned ``DRV_SUCCESS`` from a wait after a freshness
        drain, while ``GetNumberNewImages`` reported no data. Whether the cause
        is a queued notification or a short driver race, the unread-image range
        is the authoritative readiness condition.
        """

        timeout_ms = max(1, int(timeout_ms))
        deadline_s = time.perf_counter() + timeout_ms * 1.0e-3
        stale_notifications = 0

        while True:
            available = self._number_new_images()
            if available is not None:
                return available, stale_notifications

            remaining_ms = int(
                math.ceil(max(0.0, deadline_s - time.perf_counter()) * 1000.0)
            )
            if remaining_ms <= 0:
                raise AndorSDK2Error(
                    "SDK2 timed out waiting for a new continuous image after "
                    f"{timeout_ms} ms."
                )

            if getattr(self._dll, "WaitForAcquisitionTimeOut", None) is not None:
                code = self._call(
                    "WaitForAcquisitionTimeOut",
                    ctypes.c_int(remaining_ms),
                    argtypes=[ctypes.c_int],
                    accepted={DRV_SUCCESS, DRV_NO_NEW_DATA},
                )
                if code == DRV_NO_NEW_DATA:
                    raise AndorSDK2Error(
                        "SDK2 timed out waiting for a new continuous image after "
                        f"{timeout_ms} ms."
                    )
            else:
                self._call("WaitForAcquisition", argtypes=[])

            available = self._number_new_images()
            if available is not None:
                return available, stale_notifications
            stale_notifications += 1

    def _ensure_continuous_acquisition(
        self,
        *,
        exposure_ms: float,
        session: str,
    ) -> tuple[AndorAcquisitionProfile, bool, float, float, float]:
        signature = (self.settings, float(exposure_ms), str(session))
        if self._continuous_signature == signature:
            now = time.perf_counter()
            return self.last_acquisition_profile, True, now, now, 0.0

        missing = [
            name
            for name in ("GetNumberNewImages", "GetOldestImage")
            if getattr(self._dll, name, None) is None
        ]
        if missing:
            raise AndorSDK2Error(
                "SDK2 Run-Till-Abort requires circular-buffer exports missing "
                f"from this DLL: {', '.join(missing)}."
            )

        self.stop_continuous_acquisition()
        setup_started_s = time.perf_counter()
        try:
            self._call("SetAcquisitionMode", ctypes.c_int(5), argtypes=[ctypes.c_int])
            self._call("SetTriggerMode", ctypes.c_int(0), argtypes=[ctypes.c_int])
            self._call(
                "SetKineticCycleTime",
                ctypes.c_float(0.0),
                argtypes=[ctypes.c_float],
            )
            self._call(
                "SetExposureTime",
                ctypes.c_float(float(exposure_ms) * 1.0e-3),
                argtypes=[ctypes.c_float],
            )
            profile = self._read_acquisition_profile(
                requested_exposure_ms=float(exposure_ms)
            )
            self.last_acquisition_profile = profile
            prepare_started_s = time.perf_counter()
            self._call("PrepareAcquisition", argtypes=[])
            prepare_finished_s = time.perf_counter()
            start_started_s = prepare_finished_s
            self._call("StartAcquisition", argtypes=[])
            start_finished_s = time.perf_counter()
        except Exception:
            self._continuous_signature = None
            self._continuous_next_driver_index = None
            try:
                self._call("AbortAcquisition", argtypes=[])
            except Exception:
                pass
            try:
                self._call(
                    "SetAcquisitionMode",
                    ctypes.c_int(1),
                    argtypes=[ctypes.c_int],
                )
                self._call(
                    "SetTriggerMode",
                    ctypes.c_int(0),
                    argtypes=[ctypes.c_int],
                )
            except Exception:
                pass
            raise

        self._prepared_signature = None
        self._continuous_signature = signature
        self._continuous_started_s = start_started_s
        self._continuous_next_driver_index = None
        return (
            profile,
            False,
            setup_started_s,
            prepare_started_s,
            1000.0 * (start_finished_s - start_started_s),
        )

    def _acquire_continuous(
        self,
        exposure_ms: float,
        averages: int,
        *,
        session: str,
        target_midpoint_s: float,
        progress_callback: Callable[[int, int, str], None] | None,
    ) -> np.ndarray:
        profile, reused, setup_started_s, prepare_started_s, start_ms = (
            self._ensure_continuous_acquisition(
                exposure_ms=exposure_ms,
                session=session,
            )
        )
        count = self.output_pixel_count
        buffer = (ctypes.c_int32 * count)()
        discarded_before = self._drain_pending_continuous_images(buffer, count)
        timeout_ms = self._timeout_ms(exposure_ms, profile)
        average_count = max(1, int(averages))
        running_mean: np.ndarray | None = None
        wait_durations_ms: list[float] = []
        transfer_durations_ms: list[float] = []
        exposure_windows_s: list[tuple[float, float]] = []
        driver_bounds_s: list[tuple[float, float]] = []
        driver_indices: list[int] = []
        pending_after_wait = 0
        stale_wait_notifications = 0
        discarded_for_target = 0
        target_midpoint_s = float(target_midpoint_s)
        target_alignment_requested = math.isfinite(target_midpoint_s)
        target_alignment_applied = False
        if progress_callback is not None:
            progress_callback(0, average_count, "software")

        try:
            for index in range(average_count):
                frame_deadline_s = time.perf_counter() + timeout_ms * 1.0e-3
                while True:
                    remaining_ms = int(
                        math.ceil(
                            max(0.0, frame_deadline_s - time.perf_counter())
                            * 1000.0
                        )
                    )
                    if remaining_ms <= 0:
                        raise AndorSDK2Error(
                            "SDK2 timed out while selecting a continuous frame "
                            "for the requested exposure midpoint."
                        )
                    wait_started_s = time.perf_counter()
                    available, stale_count = self._wait_for_continuous_image(
                        timeout_ms=remaining_ms,
                    )
                    completed_s = time.perf_counter()
                    stale_wait_notifications += int(stale_count)
                    wait_durations_ms.append(
                        1000.0 * (completed_s - wait_started_s)
                    )

                    first, last = available
                    pending_count = last - first + 1
                    if pending_count < 1 or pending_count > 1_000_000:
                        raise AndorSDK2Error(
                            f"SDK2 returned an invalid unread image range "
                            f"{first}..{last}."
                        )
                    pending_after_wait = max(pending_after_wait, pending_count)
                    expected = self._continuous_next_driver_index
                    if expected is not None:
                        if first > expected:
                            raise AndorSDK2Error(
                                "The SDK2 circular buffer overwrote "
                                f"{first - expected} frame(s) before they could be "
                                "retrieved."
                            )
                        if first < expected:
                            raise AndorSDK2Error(
                                "The SDK2 unread image index moved backward from "
                                f"{expected} to {first}; refusing a duplicate or "
                                "stale frame."
                            )
                    self._continuous_next_driver_index = first + 1

                    transfer_started_s = time.perf_counter()
                    self._read_oldest_image(buffer, count)
                    transfer_finished_s = time.perf_counter()
                    transfer_durations_ms.append(
                        1000.0 * (transfer_finished_s - transfer_started_s)
                    )
                    values = np.ctypeslib.as_array(buffer).astype(float, copy=True)

                    exposure_window: tuple[float, float] | None = None
                    applied_s = float(profile.applied_exposure_ms) * 1.0e-3
                    readout_s = float(profile.readout_ms) * 1.0e-3
                    if (
                        math.isfinite(applied_s)
                        and applied_s > 0.0
                        and math.isfinite(readout_s)
                        and readout_s >= 0.0
                    ):
                        # GetOldestImage returns the first frame in the unread
                        # range. If GUI scheduling allowed several frames to
                        # accumulate, estimate that oldest frame's completion
                        # from its distance behind the newest frame rather than
                        # assigning every pending frame the query timestamp.
                        frame_completion_s = completed_s
                        kinetic_cycle_s = (
                            float(profile.kinetic_cycle_ms) * 1.0e-3
                        )
                        can_estimate_exposure_window = True
                        if (
                            target_alignment_requested
                            and pending_count > 1
                            and (
                                not math.isfinite(kinetic_cycle_s)
                                or kinetic_cycle_s <= 0.0
                            )
                        ):
                            # The timestamp returned by this Python call dates
                            # the newest available completion. Without a cycle
                            # time, the oldest member of a pending range cannot
                            # be dated well enough to enforce a midpoint target.
                            can_estimate_exposure_window = False
                        elif (
                            pending_count > 1
                            and math.isfinite(kinetic_cycle_s)
                            and kinetic_cycle_s > 0.0
                        ):
                            frame_completion_s -= (
                                (pending_count - 1) * kinetic_cycle_s
                            )
                        if can_estimate_exposure_window:
                            exposure_end_s = frame_completion_s - readout_s
                            exposure_start_s = exposure_end_s - applied_s
                            if exposure_start_s >= self._continuous_started_s:
                                exposure_window = (
                                    exposure_start_s,
                                    exposure_end_s,
                                )

                    # A frame can become readable after the requested deadline
                    # even though its exposure occurred entirely before it. For
                    # gated streams, skip such early-midpoint frames and return
                    # the first frame whose estimated exposure midpoint is not
                    # earlier than the requested delay. This preserves the
                    # documented midpoint timing axis without re-arming SDK2.
                    if (
                        index == 0
                        and target_alignment_requested
                        and exposure_window is None
                    ):
                        raise AndorSDK2Error(
                            "SDK2 did not provide enough exposure/readout timing "
                            "information to align a gated frame by midpoint."
                        )
                    if (
                        index == 0
                        and target_alignment_requested
                        and exposure_window is not None
                        and 0.5 * (exposure_window[0] + exposure_window[1])
                        < target_midpoint_s
                    ):
                        discarded_for_target += 1
                        target_alignment_applied = True
                        continue
                    if index == 0 and target_alignment_requested:
                        target_alignment_applied = exposure_window is not None
                    driver_indices.append(first)
                    driver_bounds_s.append((wait_started_s, completed_s))
                    if exposure_window is not None:
                        exposure_windows_s.append(exposure_window)
                    break

                if running_mean is None:
                    running_mean = values
                else:
                    running_mean += (values - running_mean) / float(index + 1)
                if progress_callback is not None:
                    progress_callback(index + 1, average_count, "software")
        except Exception as exc:
            self.stop_continuous_acquisition()
            raise AndorSDK2Error(
                f"Andor continuous acquisition failed: {exc}"
            ) from exc

        if running_mean is None:
            raise AndorSDK2Error("SDK2 returned no continuous acquisition data.")
        timing_windows = (
            exposure_windows_s
            if len(exposure_windows_s) == average_count
            else driver_bounds_s
        )
        first_window = timing_windows[0]
        last_window = timing_windows[-1]
        refined = timing_windows is exposure_windows_s
        self.last_timing = AcquisitionTiming(
            window_started_s=first_window[0],
            window_finished_s=last_window[1],
            midpoint_estimate_s=0.5 * (first_window[0] + last_window[1]),
            uncertainty_s=0.5 * max(0.0, last_window[1] - first_window[0]),
            basis=(
                "continuous_completion_minus_readout_estimate"
                if refined
                else "continuous_wait_call_bounds"
            ),
            sample_windows_s=tuple(timing_windows),
            exposure_duration_s=profile.applied_exposure_ms * 1.0e-3,
            readout_duration_s=profile.readout_ms * 1.0e-3,
            keep_clean_duration_s=profile.keep_clean_ms * 1.0e-3,
            kinetic_cycle_s=profile.kinetic_cycle_ms * 1.0e-3,
            acquisition_mode="run_till_abort",
            driver_frame_index=(driver_indices[0] if driver_indices else -1),
            discarded_frames_before=int(discarded_before),
            discarded_frames_for_target=int(discarded_for_target),
            pending_frames_after_wait=int(pending_after_wait),
        )
        self.last_stage_timing_ms = {
            "continuous_session": str(session),
            "continuous_state_reused": bool(reused),
            "settings_and_timing_query_ms": 1000.0
            * (prepare_started_s - setup_started_s),
            "prepare_ms": 0.0 if reused else 1000.0 * (
                self._continuous_started_s - prepare_started_s
            ),
            "start_command_ms": start_ms,
            "wait_ms": tuple(wait_durations_ms),
            "data_transfer_ms": tuple(transfer_durations_ms),
            "discarded_frames_before": int(discarded_before),
            "discarded_frames_for_target": int(discarded_for_target),
            "target_midpoint_s": (
                target_midpoint_s if target_alignment_requested else None
            ),
            "target_alignment_applied": bool(target_alignment_applied),
            "pending_frames_after_wait": int(pending_after_wait),
            "stale_wait_notifications": int(stale_wait_notifications),
        }
        return running_mean

    def acquire(
        self,
        exposure_ms: float,
        averages: int = 1,
        progress_callback: Callable[[int, int, str], None] | None = None,
        *,
        continuous_session: str = "",
        continuous_target_midpoint_s: float = float("nan"),
    ) -> np.ndarray:
        exposure_ms = float(exposure_ms)
        if not math.isfinite(exposure_ms) or exposure_ms <= 0:
            raise ValueError("Andor exposure time must be finite and positive.")
        session = str(continuous_session).strip()
        if session:
            self.last_stage_timing_ms = {}
            return self._acquire_continuous(
                exposure_ms,
                averages,
                session=session,
                target_midpoint_s=float(continuous_target_midpoint_s),
                progress_callback=progress_callback,
            )
        self.stop_continuous_acquisition()
        return self._acquire_single(
            exposure_ms,
            averages,
            progress_callback=progress_callback,
        )

    def _acquire_single(
        self,
        exposure_ms: float,
        averages: int = 1,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> np.ndarray:
        exposure_ms = float(exposure_ms)
        self.last_stage_timing_ms = {}
        if not math.isfinite(exposure_ms) or exposure_ms <= 0:
            raise ValueError("Andor exposure time must be finite and positive.")
        setup_started_s = time.perf_counter()
        prepared_signature = (self.settings, exposure_ms)
        prepared_state_reused = self._prepared_signature == prepared_signature
        if prepared_state_reused:
            profile = self.last_acquisition_profile
            prepare_started_s = time.perf_counter()
            prepare_finished_s = prepare_started_s
        else:
            self._call(
                "SetExposureTime",
                ctypes.c_float(exposure_ms * 1.0e-3),
                argtypes=[ctypes.c_float],
            )
            profile = self._read_acquisition_profile(
                requested_exposure_ms=exposure_ms,
            )
            self.last_acquisition_profile = profile
            prepare_started_s = time.perf_counter()
            self._call("PrepareAcquisition", argtypes=[])
            prepare_finished_s = time.perf_counter()
            self._prepared_signature = prepared_signature
        count = self.output_pixel_count
        running_mean: np.ndarray | None = None
        timing_started_s = float("nan")
        timing_finished_s = float("nan")
        driver_bound_windows_s: list[tuple[float, float]] = []
        exposure_windows_s: list[tuple[float, float]] = []
        start_command_durations_ms: list[float] = []
        wait_durations_ms: list[float] = []
        transfer_durations_ms: list[float] = []
        timeout_ms = self._timeout_ms(exposure_ms, profile)
        average_count = max(1, int(averages))
        if progress_callback is not None:
            progress_callback(0, average_count, "software")
        for index in range(average_count):
            frame_started_s = time.perf_counter()
            if index == 0:
                timing_started_s = frame_started_s
            start_command_started_s = frame_started_s
            self._call("StartAcquisition", argtypes=[])
            wait_started_s = time.perf_counter()
            start_command_durations_ms.append(
                1000.0 * (wait_started_s - start_command_started_s)
            )
            try:
                if getattr(self._dll, "WaitForAcquisitionTimeOut", None) is not None:
                    self._call(
                        "WaitForAcquisitionTimeOut",
                        ctypes.c_int(timeout_ms),
                        argtypes=[ctypes.c_int],
                    )
                else:
                    self._call("WaitForAcquisition", argtypes=[])
            except AndorSDK2Error as exc:
                self._prepared_signature = None
                try:
                    self._call("AbortAcquisition", argtypes=[])
                except AndorSDK2Error:
                    pass
                raise AndorSDK2Error(
                    f"Andor acquisition did not complete within the bounded "
                    f"wait ({timeout_ms} ms): {exc}"
                ) from exc
            wait_finished_s = time.perf_counter()
            timing_finished_s = wait_finished_s
            wait_durations_ms.append(1000.0 * (wait_finished_s - wait_started_s))
            driver_bound_windows_s.append((frame_started_s, wait_finished_s))

            applied_exposure_s = float(profile.applied_exposure_ms) * 1.0e-3
            readout_s = float(profile.readout_ms) * 1.0e-3
            if (
                math.isfinite(applied_exposure_s)
                and applied_exposure_s > 0.0
                and math.isfinite(readout_s)
                and readout_s >= 0.0
            ):
                estimated_exposure_end_s = wait_finished_s - readout_s
                estimated_exposure_start_s = (
                    estimated_exposure_end_s - applied_exposure_s
                )
                # Use the refined estimate only when it is physically
                # contained in the observed SDK call bounds. Some cameras do
                # not expose a usable GetReadOutTime value.
                if (
                    estimated_exposure_start_s >= frame_started_s
                    and estimated_exposure_end_s <= wait_finished_s
                ):
                    exposure_windows_s.append(
                        (estimated_exposure_start_s, estimated_exposure_end_s)
                    )

            transfer_started_s = time.perf_counter()
            buffer = (ctypes.c_int32 * count)()
            try:
                self._call(
                    "GetAcquiredData",
                    buffer,
                    ctypes.c_ulong(count),
                    argtypes=[ctypes.POINTER(ctypes.c_int32), ctypes.c_ulong],
                )
            except AndorSDK2Error:
                self._prepared_signature = None
                raise
            transfer_finished_s = time.perf_counter()
            transfer_durations_ms.append(
                1000.0 * (transfer_finished_s - transfer_started_s)
            )
            values = np.ctypeslib.as_array(buffer).astype(float, copy=True)
            if running_mean is None:
                running_mean = values
            else:
                running_mean += (values - running_mean) / float(index + 1)
            if progress_callback is not None:
                progress_callback(index + 1, average_count, "software")
        if running_mean is None:
            raise AndorSDK2Error("SDK2 returned no acquisition data.")
        self.last_stage_timing_ms = {
            "prepared_state_reused": prepared_state_reused,
            "settings_and_timing_query_ms": 1000.0
            * (prepare_started_s - setup_started_s),
            "prepare_ms": 1000.0 * (prepare_finished_s - prepare_started_s),
            "start_command_ms": tuple(start_command_durations_ms),
            "wait_ms": tuple(wait_durations_ms),
            "data_transfer_ms": tuple(transfer_durations_ms),
        }
        if math.isfinite(timing_started_s) and math.isfinite(timing_finished_s):
            refined = len(exposure_windows_s) == average_count
            timing_windows_s = (
                exposure_windows_s if refined else driver_bound_windows_s
            )
            window_started_s = timing_windows_s[0][0]
            window_finished_s = timing_windows_s[-1][1]
            self.last_timing = AcquisitionTiming(
                window_started_s=window_started_s,
                window_finished_s=window_finished_s,
                midpoint_estimate_s=0.5 * (window_started_s + window_finished_s),
                uncertainty_s=0.5
                * max(0.0, window_finished_s - window_started_s),
                basis=(
                    "sdk_completion_minus_readout_estimate"
                    if refined
                    else "sdk_start_to_acquisition_complete_bounds"
                ),
                sample_windows_s=tuple(timing_windows_s),
                exposure_duration_s=profile.applied_exposure_ms * 1.0e-3,
                readout_duration_s=profile.readout_ms * 1.0e-3,
                keep_clean_duration_s=profile.keep_clean_ms * 1.0e-3,
                kinetic_cycle_s=profile.kinetic_cycle_ms * 1.0e-3,
                acquisition_mode="single_scan",
            )
        else:
            self.last_timing = None
        return running_mean

    def get_temperature_c(self) -> float:
        value = ctypes.c_int()
        self._call(
            "GetTemperature",
            ctypes.byref(value),
            argtypes=[ctypes.POINTER(ctypes.c_int)],
            accepted={DRV_SUCCESS, *DRV_TEMPERATURE_CODES},
        )
        return float(value.value)

    def health_check(self) -> str:
        """Run a small camera-only query after an operation has failed."""

        serial = ctypes.c_int()
        self._call(
            "GetCameraSerialNumber",
            ctypes.byref(serial),
            argtypes=[ctypes.POINTER(ctypes.c_int)],
        )
        return str(int(serial.value))

    def set_temperature_c(self, temperature_c: float) -> None:
        value = float(temperature_c)
        if not math.isfinite(value):
            raise ValueError("Andor temperature target must be finite.")
        minimum = int(self.capabilities.temperature_min_c)
        maximum = int(self.capabilities.temperature_max_c)
        if not minimum <= value <= maximum:
            raise ValueError(
                f"Andor temperature target {value:g} °C is outside the camera "
                f"range [{minimum}, {maximum}] °C."
            )
        applied = int(round(value))
        self._call("SetTemperature", ctypes.c_int(applied), argtypes=[ctypes.c_int])
        self.temperature_target_c = float(applied)

    def set_cooler_enabled(self, enabled: bool) -> None:
        self._call("CoolerON" if enabled else "CoolerOFF", argtypes=[])
        self.capabilities = replace(
            self.capabilities,
            cooler_enabled=bool(enabled),
        )

    def stop_continuous_acquisition(self) -> None:
        """Abort an armed Run-Till-Abort stream and restore single-scan mode."""

        if self._continuous_signature is None:
            return
        self._continuous_signature = None
        self._continuous_next_driver_index = None
        self._continuous_started_s = float("nan")
        self._prepared_signature = None
        try:
            self._call(
                "AbortAcquisition",
                argtypes=[],
                accepted={DRV_SUCCESS, 20073},
            )
        finally:
            self._call(
                "SetAcquisitionMode",
                ctypes.c_int(1),
                argtypes=[ctypes.c_int],
            )
            self._call("SetTriggerMode", ctypes.c_int(0), argtypes=[ctypes.c_int])

    def close(self) -> None:
        try:
            self.stop_continuous_acquisition()
        except Exception:
            pass
        self._prepared_signature = None
        if self._initialized:
            try:
                self._call("ShutDown", argtypes=[])
            except Exception:
                pass
            self._initialized = False
        self._dll = None
        if self._dll_directory_handle is not None:
            try:
                self._dll_directory_handle.close()
            finally:
                self._dll_directory_handle = None

    def __del__(self) -> None:
        # Best-effort cleanup for partially constructed SDK objects.  Normal
        # application shutdown still calls close() explicitly on the worker.
        try:
            self.close()
        except Exception:
            pass
