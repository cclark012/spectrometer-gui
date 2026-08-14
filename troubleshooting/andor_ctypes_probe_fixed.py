from __future__ import annotations

"""Read-only ctypes probe for Andor SDK2 cameras and Kymera/Shamrock spectrographs.

This script is intended for systems where Andor Solis supplies the native DLLs but
``pyAndorSDK2`` and ``pyAndorSpectrograph`` are not installed. It initializes the
vendor SDKs, queries capabilities and current state, and shuts them down without
changing acquisition, cooler, shutter, grating, slit, or wavelength settings.

Run from the project root::

    python -m troubleshooting.andor_ctypes_probe
    python -m troubleshooting.andor_ctypes_probe --output andor_ctypes_probe.json
    python -m troubleshooting.andor_ctypes_probe \
        --solis-dir "C:\\Program Files\\Andor SOLIS" --json

Close Andor Solis before running the probe. Only one SDK application should own the
camera at a time.
"""

import argparse
import ctypes
import json
import os
import platform
import sys
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


_VERBOSE = False


def _debug(message: str) -> None:
    if _VERBOSE:
        print(str(message), file=sys.stderr, flush=True)


DRV_SUCCESS = 20002
ATSPECTROGRAPH_SUCCESS = 20202

CAMERA_RETURN_CODES = {
    20002: "DRV_SUCCESS",
    20003: "DRV_VXDNOTINSTALLED",
    20006: "DRV_ERROR_ACK",
    20010: "DRV_ERROR_PAGELOCK",
    20013: "DRV_ERROR_NOCAMERA",
    20017: "DRV_ERROR_NOHANDLE",
    20018: "DRV_GATING_NOT_AVAILABLE",
    20021: "DRV_ERROR_MAP",
    20024: "DRV_ERROR_UNMAP",
    20026: "DRV_ERROR_PAGEUNLOCK",
    20034: "DRV_TEMP_OFF",
    20035: "DRV_TEMP_NOT_STABILIZED",
    20036: "DRV_TEMP_STABILIZED",
    20037: "DRV_TEMP_NOT_REACHED",
    20038: "DRV_TEMP_OUT_RANGE",
    20039: "DRV_TEMP_NOT_SUPPORTED",
    20040: "DRV_TEMP_DRIFT",
    20049: "DRV_NOT_INITIALIZED",
    20066: "DRV_P1INVALID",
    20067: "DRV_P2INVALID",
    20068: "DRV_P3INVALID",
    20069: "DRV_P4INVALID",
    20070: "DRV_INIERROR",
    20071: "DRV_COFERROR",
    20072: "DRV_ACQUIRING",
    20075: "DRV_IDLE",
    20076: "DRV_TEMPCYCLE",
    20077: "DRV_NOT_AVAILABLE",
}

SPECTROGRAPH_RETURN_CODES = {
    20201: "ATSPECTROGRAPH_COMMUNICATION_ERROR",
    20202: "ATSPECTROGRAPH_SUCCESS",
    20249: "ATSPECTROGRAPH_ERROR",
    20266: "ATSPECTROGRAPH_P1INVALID",
    20267: "ATSPECTROGRAPH_P2INVALID",
    20268: "ATSPECTROGRAPH_P3INVALID",
    20269: "ATSPECTROGRAPH_P4INVALID",
    20270: "ATSPECTROGRAPH_P5INVALID",
    20275: "ATSPECTROGRAPH_NOT_INITIALIZED",
    20292: "ATSPECTROGRAPH_NOT_AVAILABLE",
}


class AndorCapabilities(ctypes.Structure):
    """SDK2 ``AndorCapabilities`` structure used by ``GetCapabilities``."""

    _fields_ = [
        ("ulSize", ctypes.c_ulong),
        ("ulAcqModes", ctypes.c_ulong),
        ("ulReadModes", ctypes.c_ulong),
        ("ulTriggerModes", ctypes.c_ulong),
        ("ulCameraType", ctypes.c_ulong),
        ("ulPixelMode", ctypes.c_ulong),
        ("ulSetFunctions", ctypes.c_ulong),
        ("ulGetFunctions", ctypes.c_ulong),
        ("ulFeatures", ctypes.c_ulong),
        ("ulPCICard", ctypes.c_ulong),
        ("ulEMGainCapability", ctypes.c_ulong),
        ("ulFTReadModes", ctypes.c_ulong),
        ("ulFeatures2", ctypes.c_ulong),
    ]


@dataclass(slots=True)
class CallResult:
    function: str
    available: bool
    code: int | None = None
    code_name: str = ""
    success: bool = False
    value: Any = None
    error: str = ""


@dataclass(slots=True)
class ProbeReport:
    python: str = sys.version
    architecture: tuple[str, str] = field(default_factory=platform.architecture)
    platform: str = platform.platform()
    solis_dir: str = ""
    camera_dll: str = ""
    spectrograph_dll: str = ""
    camera: dict[str, Any] = field(default_factory=dict)
    spectrograph: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class NativeLibrary:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.dll = ctypes.WinDLL(str(self.path))

    def optional(
        self,
        name: str,
        argtypes: list[Any] | None = None,
        restype: Any = ctypes.c_uint,
    ) -> Callable[..., Any] | None:
        function = getattr(self.dll, name, None)
        if function is None:
            return None
        if argtypes is not None:
            function.argtypes = argtypes
        function.restype = restype
        return function


class ResultRecorder:
    def __init__(
        self,
        code_names: dict[int, str],
        *,
        success_codes: set[int],
    ) -> None:
        self.code_names = dict(code_names)
        self.success_codes = set(success_codes)

    def unavailable(self, name: str) -> CallResult:
        return CallResult(function=name, available=False)

    def record(
        self,
        name: str,
        function: Callable[..., Any] | None,
        *args: Any,
        value: Callable[[], Any] | None = None,
        accepted_codes: set[int] | None = None,
    ) -> CallResult:
        if function is None:
            return self.unavailable(name)

        try:
            code = int(function(*args))
            valid_codes = (
                self.success_codes
                if accepted_codes is None
                else set(accepted_codes)
            )
            success = code in valid_codes
            code_name = self.code_names.get(code, f"UNKNOWN_{code}")

            # Output buffers are undefined when the native call fails. Do not
            # report their initial zero values as real instrument values.
            result_value = (
                value()
                if success and value is not None
                else None
            )

            return CallResult(
                function=name,
                available=True,
                code=code,
                code_name=code_name,
                success=success,
                value=result_value,
                error="" if success else code_name,
            )
        except Exception:
            return CallResult(
                function=name,
                available=True,
                success=False,
                error=traceback.format_exc(),
            )


def _jsonable(value: Any) -> Any:
    if isinstance(value, CallResult):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, ctypes.Array):
        return list(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _buffer_text(buffer: ctypes.Array[ctypes.c_char]) -> str:
    return bytes(buffer.value).decode("ascii", errors="replace").strip()


def _find_solis_dir(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            Path(r"C:\Program Files\Andor SOLIS"),
            Path(r"C:\Program Files (x86)\Andor SOLIS"),
            Path(r"C:\Program Files\Andor SDK"),
            Path(r"C:\Program Files (x86)\Andor SDK"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        "Could not find an Andor Solis/SDK directory. Pass --solis-dir explicitly."
    )


def _camera_dll_path(root: Path) -> Path:
    names = (
        "atmcd64d.dll",
        "atmcd64d_legacy.dll",
        "atmcd32d.dll",
        "atmcd32d_legacy.dll",
    )
    for name in names:
        candidate = root / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No SDK2 camera DLL found in {root}")


def _spectrograph_dll_path(root: Path) -> Path:
    names = (
        "atspectrograph.dll",
        "ShamrockCIF.dll",
        "shamrockcif.dll",
    )
    for name in names:
        candidate = root / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No Andor spectrograph DLL found in {root}")


def _probe_camera(root: Path, dll_path: Path) -> dict[str, Any]:
    library = NativeLibrary(dll_path)
    recorder = ResultRecorder(
        CAMERA_RETURN_CODES,
        success_codes={DRV_SUCCESS},
    )
    report: dict[str, Any] = {
        "dll": str(dll_path),
        "symbols": {},
        "available_camera_count": 0,
        "cameras": [],
    }

    get_available = library.optional(
        "GetAvailableCameras", [ctypes.POINTER(ctypes.c_long)]
    )
    get_handle = library.optional(
        "GetCameraHandle", [ctypes.c_long, ctypes.POINTER(ctypes.c_long)]
    )
    set_current = library.optional("SetCurrentCamera", [ctypes.c_long])
    initialize = library.optional("Initialize", [ctypes.c_char_p])
    shutdown = library.optional("ShutDown", [])

    core_symbols = {
        "GetAvailableCameras": get_available,
        "GetCameraHandle": get_handle,
        "SetCurrentCamera": set_current,
        "Initialize": initialize,
        "ShutDown": shutdown,
    }
    report["symbols"].update({name: fn is not None for name, fn in core_symbols.items()})

    count = ctypes.c_long()
    count_result = recorder.record(
        "GetAvailableCameras",
        get_available,
        ctypes.byref(count),
        value=lambda: int(count.value),
    )
    report["get_available_cameras"] = count_result
    if count_result.code != DRV_SUCCESS:
        return report

    camera_count = max(0, int(count.value))
    report["available_camera_count"] = camera_count

    for index in range(camera_count):
        camera: dict[str, Any] = {"index": index, "queries": {}}
        handle = ctypes.c_long()
        handle_result = recorder.record(
            "GetCameraHandle",
            get_handle,
            ctypes.c_long(index),
            ctypes.byref(handle),
            value=lambda: int(handle.value),
        )
        camera["get_handle"] = handle_result
        if handle_result.code != DRV_SUCCESS:
            report["cameras"].append(camera)
            continue

        current_result = recorder.record(
            "SetCurrentCamera", set_current, ctypes.c_long(handle.value)
        )
        camera["set_current_camera"] = current_result
        if current_result.code != DRV_SUCCESS:
            report["cameras"].append(camera)
            continue

        init_result = recorder.record(
            "Initialize", initialize, os.fsencode(str(root))
        )
        camera["initialize"] = init_result
        if init_result.code != DRV_SUCCESS:
            report["cameras"].append(camera)
            continue

        try:
            queries = camera["queries"]

            serial = ctypes.c_int()
            fn = library.optional(
                "GetCameraSerialNumber", [ctypes.POINTER(ctypes.c_int)]
            )
            queries["serial_number"] = recorder.record(
                "GetCameraSerialNumber",
                fn,
                ctypes.byref(serial),
                value=lambda: int(serial.value),
            )

            head = ctypes.create_string_buffer(256)
            fn = library.optional("GetHeadModel", [ctypes.c_char_p])
            queries["head_model"] = recorder.record(
                "GetHeadModel", fn, head, value=lambda: _buffer_text(head)
            )

            detector_x = ctypes.c_int()
            detector_y = ctypes.c_int()
            fn = library.optional(
                "GetDetector",
                [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)],
            )
            queries["detector_pixels"] = recorder.record(
                "GetDetector",
                fn,
                ctypes.byref(detector_x),
                ctypes.byref(detector_y),
                value=lambda: {"x": int(detector_x.value), "y": int(detector_y.value)},
            )

            pixel_x = ctypes.c_float()
            pixel_y = ctypes.c_float()
            fn = library.optional(
                "GetPixelSize",
                [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)],
            )
            queries["pixel_size_um"] = recorder.record(
                "GetPixelSize",
                fn,
                ctypes.byref(pixel_x),
                ctypes.byref(pixel_y),
                value=lambda: {"x": float(pixel_x.value), "y": float(pixel_y.value)},
            )

            temperature_min = ctypes.c_int()
            temperature_max = ctypes.c_int()
            fn = library.optional(
                "GetTemperatureRange",
                [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)],
            )
            queries["temperature_range_c"] = recorder.record(
                "GetTemperatureRange",
                fn,
                ctypes.byref(temperature_min),
                ctypes.byref(temperature_max),
                value=lambda: {
                    "minimum": int(temperature_min.value),
                    "maximum": int(temperature_max.value),
                },
            )

            temperature = ctypes.c_int()
            fn = library.optional("GetTemperature", [ctypes.POINTER(ctypes.c_int)])
            queries["temperature_c"] = recorder.record(
                "GetTemperature",
                fn,
                ctypes.byref(temperature),
                value=lambda: int(temperature.value),
                accepted_codes={
                    DRV_SUCCESS,
                    20034,
                    20035,
                    20036,
                    20037,
                    20038,
                    20039,
                    20040,
                },
            )

            cooler_on = ctypes.c_int()
            fn = library.optional("IsCoolerOn", [ctypes.POINTER(ctypes.c_int)])
            queries["cooler_on"] = recorder.record(
                "IsCoolerOn",
                fn,
                ctypes.byref(cooler_on),
                value=lambda: bool(cooler_on.value),
            )

            capabilities = AndorCapabilities()
            capabilities.ulSize = ctypes.sizeof(AndorCapabilities)
            fn = library.optional(
                "GetCapabilities", [ctypes.POINTER(AndorCapabilities)]
            )
            queries["capabilities"] = recorder.record(
                "GetCapabilities",
                fn,
                ctypes.byref(capabilities),
                value=lambda: {
                    name: int(getattr(capabilities, name))
                    for name, _ctype in capabilities._fields_
                },
            )

            ad_channels = ctypes.c_int()
            fn = library.optional(
                "GetNumberADChannels", [ctypes.POINTER(ctypes.c_int)]
            )
            queries["ad_channel_count"] = recorder.record(
                "GetNumberADChannels",
                fn,
                ctypes.byref(ad_channels),
                value=lambda: int(ad_channels.value),
            )

            amplifiers = ctypes.c_int()
            fn = library.optional("GetNumberAmp", [ctypes.POINTER(ctypes.c_int)])
            queries["amplifier_count"] = recorder.record(
                "GetNumberAmp",
                fn,
                ctypes.byref(amplifiers),
                value=lambda: int(amplifiers.value),
            )

            vs_count = ctypes.c_int()
            get_vs_count = library.optional(
                "GetNumberVSSpeeds", [ctypes.POINTER(ctypes.c_int)]
            )
            vs_count_result = recorder.record(
                "GetNumberVSSpeeds",
                get_vs_count,
                ctypes.byref(vs_count),
                value=lambda: int(vs_count.value),
            )
            queries["vertical_shift_speed_count"] = vs_count_result
            get_vs_speed = library.optional(
                "GetVSSpeed", [ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
            )
            vertical_speeds: list[dict[str, Any]] = []
            if vs_count_result.code == DRV_SUCCESS:
                for speed_index in range(max(0, int(vs_count.value))):
                    speed = ctypes.c_float()
                    result = recorder.record(
                        "GetVSSpeed",
                        get_vs_speed,
                        ctypes.c_int(speed_index),
                        ctypes.byref(speed),
                        value=lambda speed=speed, speed_index=speed_index: {
                            "index": speed_index,
                            "microseconds_per_pixel": float(speed.value),
                        },
                    )
                    vertical_speeds.append(_jsonable(result))
            queries["vertical_shift_speeds"] = vertical_speeds

            fastest_index = ctypes.c_int()
            fastest_speed = ctypes.c_float()
            fn = library.optional(
                "GetFastestRecommendedVSSpeed",
                [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_float)],
            )
            queries["fastest_recommended_vertical_shift"] = recorder.record(
                "GetFastestRecommendedVSSpeed",
                fn,
                ctypes.byref(fastest_index),
                ctypes.byref(fastest_speed),
                value=lambda: {
                    "index": int(fastest_index.value),
                    "microseconds_per_pixel": float(fastest_speed.value),
                },
            )

            get_hs_count = library.optional(
                "GetNumberHSSpeeds",
                [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
            )
            get_hs_speed = library.optional(
                "GetHSSpeed",
                [
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_float),
                ],
            )
            horizontal_speeds: list[dict[str, Any]] = []
            for ad_index in range(max(0, int(ad_channels.value))):
                for amplifier_index in range(max(0, int(amplifiers.value))):
                    hs_count = ctypes.c_int()
                    count_call = recorder.record(
                        "GetNumberHSSpeeds",
                        get_hs_count,
                        ctypes.c_int(ad_index),
                        ctypes.c_int(amplifier_index),
                        ctypes.byref(hs_count),
                        value=lambda: int(hs_count.value),
                    )
                    entry: dict[str, Any] = {
                        "ad_channel": ad_index,
                        "amplifier": amplifier_index,
                        "count_call": _jsonable(count_call),
                        "speeds_mhz": [],
                    }
                    if count_call.code == DRV_SUCCESS:
                        for speed_index in range(max(0, int(hs_count.value))):
                            speed = ctypes.c_float()
                            speed_call = recorder.record(
                                "GetHSSpeed",
                                get_hs_speed,
                                ctypes.c_int(ad_index),
                                ctypes.c_int(amplifier_index),
                                ctypes.c_int(speed_index),
                                ctypes.byref(speed),
                                value=lambda speed=speed, speed_index=speed_index: {
                                    "index": speed_index,
                                    "mhz": float(speed.value),
                                },
                            )
                            entry["speeds_mhz"].append(_jsonable(speed_call))
                    horizontal_speeds.append(entry)
            queries["horizontal_shift_speeds"] = horizontal_speeds

            internal_shutter = ctypes.c_int()
            fn = library.optional(
                "IsInternalMechanicalShutter", [ctypes.POINTER(ctypes.c_int)]
            )
            queries["internal_mechanical_shutter"] = recorder.record(
                "IsInternalMechanicalShutter",
                fn,
                ctypes.byref(internal_shutter),
                value=lambda: bool(internal_shutter.value),
            )

            shutter_close = ctypes.c_int()
            shutter_open = ctypes.c_int()
            fn = library.optional(
                "GetShutterMinTimes",
                [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)],
            )
            queries["shutter_minimum_times_ms"] = recorder.record(
                "GetShutterMinTimes",
                fn,
                ctypes.byref(shutter_close),
                ctypes.byref(shutter_open),
                value=lambda: {
                    "closing": int(shutter_close.value),
                    "opening": int(shutter_open.value),
                },
            )

            maximum_binning: list[dict[str, Any]] = []
            fn = library.optional(
                "GetMaximumBinning",
                [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
            )
            for read_mode in range(5):
                for dimension in range(2):
                    maximum = ctypes.c_int()
                    result = recorder.record(
                        "GetMaximumBinning",
                        fn,
                        ctypes.c_int(read_mode),
                        ctypes.c_int(dimension),
                        ctypes.byref(maximum),
                        value=lambda read_mode=read_mode, dimension=dimension, maximum=maximum: {
                            "read_mode": read_mode,
                            "dimension": dimension,
                            "maximum": int(maximum.value),
                        },
                    )
                    maximum_binning.append(_jsonable(result))
            queries["maximum_binning"] = maximum_binning

            exposure = ctypes.c_float()
            accumulation = ctypes.c_float()
            kinetic = ctypes.c_float()
            fn = library.optional(
                "GetAcquisitionTimings",
                [
                    ctypes.POINTER(ctypes.c_float),
                    ctypes.POINTER(ctypes.c_float),
                    ctypes.POINTER(ctypes.c_float),
                ],
            )
            queries["current_acquisition_timings_s"] = recorder.record(
                "GetAcquisitionTimings",
                fn,
                ctypes.byref(exposure),
                ctypes.byref(accumulation),
                ctypes.byref(kinetic),
                value=lambda: {
                    "exposure": float(exposure.value),
                    "accumulation": float(accumulation.value),
                    "kinetic_cycle": float(kinetic.value),
                },
            )

            hardware_values = [ctypes.c_uint() for _ in range(6)]
            fn = library.optional(
                "GetHardwareVersion", [ctypes.POINTER(ctypes.c_uint)] * 6
            )
            queries["hardware_version"] = recorder.record(
                "GetHardwareVersion",
                fn,
                *(ctypes.byref(value) for value in hardware_values),
                value=lambda: [int(value.value) for value in hardware_values],
            )

            software_values = [ctypes.c_uint() for _ in range(6)]
            fn = library.optional(
                "GetSoftwareVersion", [ctypes.POINTER(ctypes.c_uint)] * 6
            )
            queries["software_version"] = recorder.record(
                "GetSoftwareVersion",
                fn,
                *(ctypes.byref(value) for value in software_values),
                value=lambda: [int(value.value) for value in software_values],
            )
        finally:
            camera["shutdown"] = recorder.record("ShutDown", shutdown)

        report["cameras"].append(camera)

    return report


def _spectrograph_prefix(library: NativeLibrary) -> str | None:
    if getattr(library.dll, "ATSpectrographInitialize", None) is not None:
        return "ATSpectrograph"
    if getattr(library.dll, "ShamrockInitialize", None) is not None:
        return "Shamrock"
    return None


def _probe_spectrograph(root: Path, dll_path: Path) -> dict[str, Any]:
    _debug("Probing spectrograph...")
    library = NativeLibrary(dll_path)
    recorder = ResultRecorder(
        SPECTROGRAPH_RETURN_CODES,
        success_codes={ATSPECTROGRAPH_SUCCESS},
    )
    prefix = _spectrograph_prefix(library)
    report: dict[str, Any] = {
        "dll": str(dll_path),
        "prefix": prefix,
        "symbols": {},
        "devices": [],
    }
    if prefix is None:
        report["error"] = "Neither ATSpectrograph nor Shamrock exports were found."
        return report

    def name(suffix: str) -> str:
        return f"{prefix}{suffix}"

    def optional(suffix: str, argtypes: list[Any] | None = None):
        function_name = name(suffix)
        function = library.optional(function_name, argtypes)
        report["symbols"][function_name] = function is not None
        return function_name, function

    initialize_name, initialize = optional("Initialize", [ctypes.c_char_p])
    close_name, close = optional("Close", [])
    count_name, get_count = optional(
        "GetNumberDevices", [ctypes.POINTER(ctypes.c_int)]
    )

    init_result = recorder.record(initialize_name, initialize, os.fsencode(str(root)))
    report["initialize"] = init_result
    if init_result.code != ATSPECTROGRAPH_SUCCESS:
        _debug("Spectrograph failed to initialize...")
        return report

    _debug("Spectrograph initialized...")

    try:
        count = ctypes.c_int()
        count_result = recorder.record(
            count_name,
            get_count,
            ctypes.byref(count),
            value=lambda: int(count.value),
        )
        report["get_number_devices"] = count_result
        if count_result.code != ATSPECTROGRAPH_SUCCESS:
            _debug("Spectrograph numbering failed...")
            return report

        for device_index in range(max(0, int(count.value))):
            device: dict[str, Any] = {"index": device_index, "queries": {}}
            _debug(f"Spectrograph device index: {device_index}")
            queries = device["queries"]

            function_name, function = optional(
                "GetSerialNumber", [ctypes.c_int, ctypes.c_char_p]
            )
            serial = ctypes.create_string_buffer(256)
            
            queries["serial_number"] = recorder.record(
                function_name,
                function,
                ctypes.c_int(device_index),
                serial,
                value=lambda: _buffer_text(serial),
            )

            function_name, function = optional(
                "GetNumberGratings", [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
            )
            grating_count = ctypes.c_int()
            
            grating_count_result = recorder.record(
                function_name,
                function,
                ctypes.c_int(device_index),
                ctypes.byref(grating_count),
                value=lambda: int(grating_count.value),
            )
            queries["grating_count"] = grating_count_result

            function_name, function = optional(
                "GetGrating", [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
            )
            current_grating = ctypes.c_int()
            
            queries["current_grating"] = recorder.record(
                function_name,
                function,
                ctypes.c_int(device_index),
                ctypes.byref(current_grating),
                value=lambda: int(current_grating.value),
            )

            function_name, function = optional(
                "GetWavelength", [ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
            )
            wavelength = ctypes.c_float()
            
            queries["wavelength_nm"] = recorder.record(
                function_name,
                function,
                ctypes.c_int(device_index),
                ctypes.byref(wavelength),
                value=lambda: float(wavelength.value),
            )

            function_name, function = optional(
                "GetTurret", [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
            )
            turret = ctypes.c_int()
            
            queries["turret"] = recorder.record(
                function_name,
                function,
                ctypes.c_int(device_index),
                ctypes.byref(turret),
                value=lambda: int(turret.value),
            )

            function_name, function = optional(
                "EepromGetOpticalParams",
                [
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_float),
                    ctypes.POINTER(ctypes.c_float),
                    ctypes.POINTER(ctypes.c_float),
                ],
            )
            focal_length = ctypes.c_float()
            angular_deviation = ctypes.c_float()
            focal_tilt = ctypes.c_float()
            
            
            
            queries["optical_parameters"] = recorder.record(
                function_name,
                function,
                ctypes.c_int(device_index),
                ctypes.byref(focal_length),
                ctypes.byref(angular_deviation),
                ctypes.byref(focal_tilt),
                value=lambda: {
                    "focal_length_mm": float(focal_length.value),
                    "angular_deviation": float(angular_deviation.value),
                    "focal_tilt": float(focal_tilt.value),
                },
            )

            gratings: list[dict[str, Any]] = []
            get_info_name, get_info = optional(
                "GetGratingInfo",
                [
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_float),
                    ctypes.c_char_p,
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_int),
                ],
            )
            limits_name, get_limits = optional(
                "GetWavelengthLimits",
                [
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_float),
                    ctypes.POINTER(ctypes.c_float),
                ],
            )
            for grating_index in range(1, max(0, int(grating_count.value)) + 1):
                lines = ctypes.c_float()
                blaze = ctypes.create_string_buffer(256)
                home = ctypes.c_int()
                offset = ctypes.c_int()
                info_result = recorder.record(
                    get_info_name,
                    get_info,
                    ctypes.c_int(device_index),
                    ctypes.c_int(grating_index),
                    ctypes.byref(lines),
                    blaze,
                    ctypes.byref(home),
                    ctypes.byref(offset),
                    value=lambda lines=lines, blaze=blaze, home=home, offset=offset: {
                        "lines_per_mm": float(lines.value),
                        "blaze": _buffer_text(blaze),
                        "home": int(home.value),
                        "offset": int(offset.value),
                    },
                )
                minimum = ctypes.c_float()
                maximum = ctypes.c_float()
                limits_result = recorder.record(
                    limits_name,
                    get_limits,
                    ctypes.c_int(device_index),
                    ctypes.c_int(grating_index),
                    ctypes.byref(minimum),
                    ctypes.byref(maximum),
                    value=lambda minimum=minimum, maximum=maximum: {
                        "minimum_nm": float(minimum.value),
                        "maximum_nm": float(maximum.value),
                    },
                )
                gratings.append(
                    {
                        "index": grating_index,
                        "info": _jsonable(info_result),
                        "wavelength_limits": _jsonable(limits_result),
                    }
                )
            queries["gratings"] = gratings

            auto_slits: list[dict[str, Any]] = []
            present_name, auto_slit_present = optional(
                "AutoSlitIsPresent",
                [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
            )
            width_name, get_auto_slit_width = optional(
                "GetAutoSlitWidth",
                [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_float)],
            )
            for slit_index in range(1, 5):
                present = ctypes.c_int()
                present_result = recorder.record(
                    present_name,
                    auto_slit_present,
                    ctypes.c_int(device_index),
                    ctypes.c_int(slit_index),
                    ctypes.byref(present),
                    value=lambda present=present: bool(present.value),
                )
                slit: dict[str, Any] = {
                    "index": slit_index,
                    "present": _jsonable(present_result),
                }
                if present_result.code == ATSPECTROGRAPH_SUCCESS and present.value:
                    width = ctypes.c_float()
                    slit["width_um"] = _jsonable(
                        recorder.record(
                            width_name,
                            get_auto_slit_width,
                            ctypes.c_int(device_index),
                            ctypes.c_int(slit_index),
                            ctypes.byref(width),
                            value=lambda width=width: float(width.value),
                        )
                    )
                auto_slits.append(slit)
            queries["auto_slits"] = auto_slits

            shutter_present_name, shutter_present_fn = optional(
                "ShutterIsPresent", [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
            )
            shutter_present = ctypes.c_int()
            shutter_present_result = recorder.record(
                shutter_present_name,
                shutter_present_fn,
                ctypes.c_int(device_index),
                ctypes.byref(shutter_present),
                value=lambda: bool(shutter_present.value),
            )
            queries["shutter_present"] = shutter_present_result
            if shutter_present_result.code == ATSPECTROGRAPH_SUCCESS and shutter_present.value:
                shutter_name, get_shutter = optional(
                    "GetShutter", [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
                )
                shutter = ctypes.c_int()
                queries["shutter_state"] = recorder.record(
                    shutter_name,
                    get_shutter,
                    ctypes.c_int(device_index),
                    ctypes.byref(shutter),
                    value=lambda: int(shutter.value),
                )

            flippers: list[dict[str, Any]] = []
            present_name, flipper_present_fn = optional(
                "FlipperMirrorIsPresent",
                [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
            )
            position_name, get_flipper = optional(
                "GetFlipperMirror",
                [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
            )
            for flipper_index in (1, 2):
                present = ctypes.c_int()
                present_result = recorder.record(
                    present_name,
                    flipper_present_fn,
                    ctypes.c_int(device_index),
                    ctypes.c_int(flipper_index),
                    ctypes.byref(present),
                    value=lambda present=present: bool(present.value),
                )
                entry: dict[str, Any] = {
                    "index": flipper_index,
                    "present": _jsonable(present_result),
                }
                if present_result.code == ATSPECTROGRAPH_SUCCESS and present.value:
                    position = ctypes.c_int()
                    entry["position"] = _jsonable(
                        recorder.record(
                            position_name,
                            get_flipper,
                            ctypes.c_int(device_index),
                            ctypes.c_int(flipper_index),
                            ctypes.byref(position),
                            value=lambda position=position: int(position.value),
                        )
                    )
                flippers.append(entry)
            queries["flipper_mirrors"] = flippers

            focus_present_name, focus_present_fn = optional(
                "FocusMirrorIsPresent",
                [ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
            )
            focus_present = ctypes.c_int()
            focus_present_result = recorder.record(
                focus_present_name,
                focus_present_fn,
                ctypes.c_int(device_index),
                ctypes.byref(focus_present),
                value=lambda: bool(focus_present.value),
            )
            queries["focus_mirror_present"] = focus_present_result
            if focus_present_result.code == ATSPECTROGRAPH_SUCCESS and focus_present.value:
                focus_name, get_focus = optional(
                    "GetFocusMirror", [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
                )
                focus = ctypes.c_int()
                queries["focus_mirror_position"] = recorder.record(
                    focus_name,
                    get_focus,
                    ctypes.c_int(device_index),
                    ctypes.byref(focus),
                    value=lambda: int(focus.value),
                )

            detector_offset_name, get_detector_offset = optional(
                "GetDetectorOffset",
                [
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_int),
                ],
            )
            detector_offsets: list[dict[str, Any]] = []
            for entrance_port in (0, 1):
                for exit_port in (0, 1):
                    detector_offset = ctypes.c_int()
                    result = recorder.record(
                        detector_offset_name,
                        get_detector_offset,
                        ctypes.c_int(device_index),
                        ctypes.c_int(entrance_port),
                        ctypes.c_int(exit_port),
                        ctypes.byref(detector_offset),
                        value=lambda detector_offset=detector_offset: int(
                            detector_offset.value
                        ),
                    )
                    detector_offsets.append(
                        {
                            "entrance_port": entrance_port,
                            "exit_port": exit_port,
                            "result": _jsonable(result),
                        }
                    )
            queries["detector_offsets"] = detector_offsets

            pixel_width_name, get_pixel_width = optional(
                "GetPixelWidth", [ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
            )
            pixel_width = ctypes.c_float()
            queries["pixel_width_um"] = recorder.record(
                pixel_width_name,
                get_pixel_width,
                ctypes.c_int(device_index),
                ctypes.byref(pixel_width),
                value=lambda: float(pixel_width.value),
            )

            pixel_count_name, get_pixel_count = optional(
                "GetNumberPixels", [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
            )
            pixel_count = ctypes.c_int()
            pixel_count_result = recorder.record(
                pixel_count_name,
                get_pixel_count,
                ctypes.c_int(device_index),
                ctypes.byref(pixel_count),
                value=lambda: int(pixel_count.value),
            )
            queries["pixel_count"] = pixel_count_result

            calibration_name, get_calibration = optional(
                "GetCalibration",
                [ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int],
            )
            if (
                pixel_count_result.code == ATSPECTROGRAPH_SUCCESS
                and 0 < pixel_count.value <= 100_000
            ):
                calibration = (ctypes.c_float * int(pixel_count.value))()
                calibration_result = recorder.record(
                    calibration_name,
                    get_calibration,
                    ctypes.c_int(device_index),
                    calibration,
                    ctypes.c_int(pixel_count.value),
                    value=lambda: {
                        "count": int(pixel_count.value),
                        "first_nm": float(calibration[0]),
                        "middle_nm": float(calibration[int(pixel_count.value) // 2]),
                        "last_nm": float(calibration[int(pixel_count.value) - 1]),
                    },
                )
                queries["calibration_summary"] = calibration_result

            report["devices"].append(device)
    finally:
        
        report["close"] = recorder.record(close_name, close)

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only ctypes probe for Andor SDK2 and Kymera/Shamrock DLLs."
    )
    parser.add_argument(
        "--solis-dir",
        default=None,
        help="Directory containing atmcd64d.dll and atspectrograph.dll.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write machine-readable JSON to stdout.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write the complete JSON report directly to this file.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Write progress diagnostics to stderr.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        print("This probe requires Windows.", file=sys.stderr)
        return 2
    args = parse_args(argv)

    global _VERBOSE
    _VERBOSE = bool(args.verbose)
    _debug("Probing Andor capabilities...")
    report = ProbeReport()
    dll_handle = None

    try:
        root = _find_solis_dir(args.solis_dir)
        report.solis_dir = str(root)
        dll_handle = os.add_dll_directory(str(root))

        camera_path = _camera_dll_path(root)
        spectrograph_path = _spectrograph_dll_path(root)
        report.camera_dll = str(camera_path)
        report.spectrograph_dll = str(spectrograph_path)
        _debug(f"Root: {root}")

        _debug("Probing camera...")
        try:
            report.camera = _probe_camera(root, camera_path)
            _debug("Camera probe complete.")
        except Exception:
            report.camera = {"fatal_error": traceback.format_exc()}
            _debug("Camera probe failed.")

        try:
            report.spectrograph = _probe_spectrograph(root, spectrograph_path)
            _debug("Spectrograph probe complete.")
        except Exception:
            report.spectrograph = {"fatal_error": traceback.format_exc()}
            _debug("Spectrograph probe failed.")
    except Exception:
        report.warnings.append(traceback.format_exc())
    finally:
        _debug("Releasing DLL search path handle...")
        if dll_handle is not None:
            try:
                dll_handle.close()
            except Exception:
                pass

    payload = _jsonable(asdict(report))
    json_text = json.dumps(payload, indent=2) + "\n"

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json_text, encoding="utf-8")
        _debug(f"Wrote JSON report to {output_path}")

    _debug("Finished probing.")
    if args.json:
        # JSON mode writes no progress text to stdout, so shell redirection
        # produces a valid JSON document. Diagnostics remain on stderr.
        sys.stdout.write(json_text)
    elif not args.output:
        print(f"Python: {report.python}")
        print(f"Platform: {report.platform}")
        print(f"Solis directory: {report.solis_dir or '--'}")
        print(f"Camera DLL: {report.camera_dll or '--'}")
        print(f"Spectrograph DLL: {report.spectrograph_dll or '--'}")
        print(
            "Cameras found:",
            report.camera.get("available_camera_count", 0)
            if isinstance(report.camera, dict)
            else 0,
        )
        print(
            "Spectrographs found:",
            len(report.spectrograph.get("devices", []))
            if isinstance(report.spectrograph, dict)
            else 0,
        )
        if report.warnings:
            print("Warnings:")
            for warning in report.warnings:
                print(warning)
        print("Run with --json or --output for the complete report.")
    elif args.output:
        print(f"Wrote Andor probe report to: {Path(args.output).resolve()}")

    has_camera = bool(report.camera.get("available_camera_count", 0))
    has_spectrograph = bool(report.spectrograph.get("devices", []))
    return 0 if has_camera or has_spectrograph else 1


if __name__ == "__main__":
    raise SystemExit(main())
