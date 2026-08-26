from __future__ import annotations

import ctypes
import math
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

DRV_SUCCESS = 20002
DRV_TEMPERATURE_CODES = {20034, 20035, 20036, 20037, 20038, 20039, 20040}


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

    def as_control_schema(self) -> dict[str, Any]:
        return asdict(self)


class AndorSDK2Camera:
    """Small SDK2 wrapper for single-scan, full-vertical-binning spectroscopy."""

    DLL_NAMES = ("atmcd64d.dll", "atmcd64d_legacy.dll", "atmcd32d.dll")

    def __init__(
        self,
        solis_dir: str | Path,
        *,
        camera_index: int = 0,
        settings: AndorCameraSettings | None = None,
    ) -> None:
        self.solis_dir = Path(solis_dir)
        self.camera_index = int(camera_index)
        self.settings = settings or AndorCameraSettings()
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
            self.apply_settings(self.settings)
        except Exception:
            self.close()
            raise

    def _load_dll(self):
        if not hasattr(ctypes, "WinDLL"):
            raise AndorSDK2Error(
                "The Andor SDK2 native adapter is available only on Windows."
            )
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
        return ctypes.WinDLL(str(dll_path))

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
            raise AndorSDK2Error(f"{name} failed with SDK2 return code {code}.")
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

    def acquire(self, exposure_ms: float, averages: int = 1) -> np.ndarray:
        exposure_ms = float(exposure_ms)
        if not math.isfinite(exposure_ms) or exposure_ms <= 0:
            raise ValueError("Andor exposure time must be finite and positive.")
        self._call(
            "SetExposureTime",
            ctypes.c_float(exposure_ms * 1.0e-3),
            argtypes=[ctypes.c_float],
        )
        self._call("PrepareAcquisition", argtypes=[])
        count = self.output_pixel_count
        running_mean: np.ndarray | None = None
        timeout_ms = min(
            2_147_000_000,
            max(10_000, int(math.ceil(2.0 * exposure_ms + 10_000.0))),
        )
        for index in range(max(1, int(averages))):
            self._call("StartAcquisition", argtypes=[])
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
                try:
                    self._call("AbortAcquisition", argtypes=[])
                except AndorSDK2Error:
                    pass
                raise AndorSDK2Error(
                    f"Andor acquisition did not complete within the bounded "
                    f"wait ({timeout_ms} ms): {exc}"
                ) from exc
            buffer = (ctypes.c_int32 * count)()
            self._call(
                "GetAcquiredData",
                buffer,
                ctypes.c_ulong(count),
                argtypes=[ctypes.POINTER(ctypes.c_int32), ctypes.c_ulong],
            )
            values = np.ctypeslib.as_array(buffer).astype(float, copy=True)
            if running_mean is None:
                running_mean = values
            else:
                running_mean += (values - running_mean) / float(index + 1)
        if running_mean is None:
            raise AndorSDK2Error("SDK2 returned no acquisition data.")
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
        self._call("SetTemperature", ctypes.c_int(round(value)), argtypes=[ctypes.c_int])

    def set_cooler_enabled(self, enabled: bool) -> None:
        self._call("CoolerON" if enabled else "CoolerOFF", argtypes=[])

    def close(self) -> None:
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
