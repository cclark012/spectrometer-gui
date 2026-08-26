from __future__ import annotations

import ctypes
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

ATSPECTROGRAPH_SUCCESS = 20202


class AndorSpectrographError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AndorGratingInfo:
    index: int
    lines_per_mm: float
    blaze: str
    home: int
    offset: int
    minimum_nm: float
    maximum_nm: float


@dataclass(frozen=True, slots=True)
class KymeraCapabilities:
    serial_number: str
    gratings: tuple[AndorGratingInfo, ...]
    filter_wheel_present: bool
    filter_positions: tuple[int, ...]
    flipper_mirrors: dict[int, tuple[int, ...]]
    focus_mirror_present: bool
    focus_mirror_max_steps: int
    detector_offsets: dict[str, int]

    def as_control_schema(self) -> dict[str, Any]:
        return asdict(self)


class AndorKymera:
    DLL_NAMES = ("atspectrograph.dll", "ShamrockCIF.dll", "shamrockcif.dll")

    def __init__(self, solis_dir: str | Path, *, device_index: int = 0) -> None:
        self.solis_dir = Path(solis_dir)
        self.device_index = int(device_index)
        self._initialized = False
        self._dll_directory_handle = None
        self._dll = self._load_dll()
        self.prefix = (
            "ATSpectrograph"
            if getattr(self._dll, "ATSpectrographInitialize", None) is not None
            else "Shamrock"
        )
        try:
            self._call(
                "Initialize",
                os.fsencode(str(self.solis_dir)),
                argtypes=[ctypes.c_char_p],
            )
            self._initialized = True
            count = ctypes.c_int()
            self._call(
                "GetNumberDevices",
                ctypes.byref(count),
                argtypes=[ctypes.POINTER(ctypes.c_int)],
            )
            if not 0 <= self.device_index < int(count.value):
                if count.value <= 0:
                    raise AndorSpectrographError(
                        "ATSpectrograph reported no connected Kymera devices."
                    )
                raise AndorSpectrographError(
                    f"Kymera index {self.device_index} is outside 0..{int(count.value) - 1}."
                )
            self.capabilities = self._read_capabilities()
        except Exception:
            self.close()
            raise

    def _load_dll(self):
        if not hasattr(ctypes, "WinDLL"):
            raise AndorSpectrographError(
                "The Andor spectrograph native adapter is available only on Windows."
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
            raise FileNotFoundError(
                f"No Andor spectrograph DLL found in {self.solis_dir}"
            )
        if hasattr(os, "add_dll_directory"):
            self._dll_directory_handle = os.add_dll_directory(str(self.solis_dir))
        return ctypes.WinDLL(str(dll_path))

    def _function(self, suffix: str, argtypes: list[Any], *, required: bool = True):
        name = f"{self.prefix}{suffix}"
        function = getattr(self._dll, name, None)
        if function is None:
            if required:
                raise AndorSpectrographError(
                    f"Andor spectrograph DLL does not export {name}."
                )
            return None
        function.argtypes = argtypes
        function.restype = ctypes.c_uint
        return function

    def _call(
        self,
        suffix: str,
        *args: Any,
        argtypes: list[Any],
        required: bool = True,
    ) -> int | None:
        function = self._function(suffix, argtypes, required=required)
        if function is None:
            return None
        code = int(function(*args))
        if code != ATSPECTROGRAPH_SUCCESS:
            raise AndorSpectrographError(
                f"{self.prefix}{suffix} failed with return code {code}."
            )
        return code

    def _serial_number(self) -> str:
        buffer_size = 256
        buffer = ctypes.create_string_buffer(buffer_size)
        args: list[Any] = [ctypes.c_int(self.device_index), buffer]
        argtypes: list[Any] = [ctypes.c_int, ctypes.POINTER(ctypes.c_char)]
        if self.prefix == "ATSpectrograph":
            args.append(ctypes.c_int(buffer_size))
            argtypes.append(ctypes.c_int)
        self._call("GetSerialNumber", *args, argtypes=argtypes)
        return bytes(buffer.value).decode("ascii", errors="replace").strip()

    def _grating_info(self, index: int) -> AndorGratingInfo:
        buffer_size = 256
        lines = ctypes.c_float()
        blaze = ctypes.create_string_buffer(buffer_size)
        home = ctypes.c_int()
        offset = ctypes.c_int()
        args: list[Any] = [
            ctypes.c_int(self.device_index),
            ctypes.c_int(index),
            ctypes.byref(lines),
            blaze,
        ]
        argtypes: list[Any] = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_char),
        ]
        if self.prefix == "ATSpectrograph":
            args.append(ctypes.c_int(buffer_size))
            argtypes.append(ctypes.c_int)
        args.extend([ctypes.byref(home), ctypes.byref(offset)])
        argtypes.extend([ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)])
        self._call("GetGratingInfo", *args, argtypes=argtypes)

        minimum = ctypes.c_float()
        maximum = ctypes.c_float()
        self._call(
            "GetWavelengthLimits",
            ctypes.c_int(self.device_index),
            ctypes.c_int(index),
            ctypes.byref(minimum),
            ctypes.byref(maximum),
            argtypes=[
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
            ],
        )
        return AndorGratingInfo(
            index=int(index),
            lines_per_mm=float(lines.value),
            blaze=bytes(blaze.value).decode("ascii", errors="replace").strip(),
            home=int(home.value),
            offset=int(offset.value),
            minimum_nm=float(minimum.value),
            maximum_nm=float(maximum.value),
        )

    def _present(self, suffix: str, *indices: int) -> bool:
        present = ctypes.c_int()
        args = [
            ctypes.c_int(self.device_index),
            *(ctypes.c_int(value) for value in indices),
        ]
        args.append(ctypes.byref(present))
        argtypes = [
            ctypes.c_int,
            *([ctypes.c_int] * len(indices)),
            ctypes.POINTER(ctypes.c_int),
        ]
        code = self._call(suffix, *args, argtypes=argtypes, required=False)
        return code is not None and bool(present.value)

    def _read_capabilities(self) -> KymeraCapabilities:
        grating_count = ctypes.c_int()
        self._call(
            "GetNumberGratings",
            ctypes.c_int(self.device_index),
            ctypes.byref(grating_count),
            argtypes=[ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
        )
        gratings = tuple(
            self._grating_info(index)
            for index in range(1, max(0, int(grating_count.value)) + 1)
        )
        filter_present = self._present("FilterIsPresent")
        flippers: dict[int, tuple[int, ...]] = {}
        for flipper in (1, 2):
            if self._present("FlipperMirrorIsPresent", flipper):
                flippers[flipper] = (0, 1)
        focus_present = self._present("FocusMirrorIsPresent")
        max_steps = ctypes.c_int()
        if focus_present:
            code = self._call(
                "GetFocusMirrorMaxSteps",
                ctypes.c_int(self.device_index),
                ctypes.byref(max_steps),
                argtypes=[ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
                required=False,
            )
            if code is None:
                max_steps.value = 0
        detector_offsets: dict[str, int] = {}
        try:
            for entrance_port in (0, 1):
                for exit_port in (0, 1):
                    detector_offsets[f"{entrance_port}:{exit_port}"] = self._get_int(
                        "GetDetectorOffset",
                        entrance_port,
                        exit_port,
                    )
        except AndorSpectrographError:
            # Older DLLs may omit detector-offset access. Keep the advanced
            # setter available, but do not invent current values.
            detector_offsets = {}
        return KymeraCapabilities(
            serial_number=self._serial_number(),
            gratings=gratings,
            filter_wheel_present=filter_present,
            filter_positions=tuple(range(1, 7)) if filter_present else (),
            flipper_mirrors=flippers,
            focus_mirror_present=focus_present,
            focus_mirror_max_steps=max(0, int(max_steps.value)),
            detector_offsets=detector_offsets,
        )

    def _get_int(self, suffix: str, *indices: int) -> int:
        value = ctypes.c_int()
        args = [
            ctypes.c_int(self.device_index),
            *(ctypes.c_int(item) for item in indices),
        ]
        args.append(ctypes.byref(value))
        self._call(
            suffix,
            *args,
            argtypes=[
                ctypes.c_int,
                *([ctypes.c_int] * len(indices)),
                ctypes.POINTER(ctypes.c_int),
            ],
        )
        return int(value.value)

    def _get_float(self, suffix: str) -> float:
        value = ctypes.c_float()
        self._call(
            suffix,
            ctypes.c_int(self.device_index),
            ctypes.byref(value),
            argtypes=[ctypes.c_int, ctypes.POINTER(ctypes.c_float)],
        )
        return float(value.value)

    def state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "grating": self._get_int("GetGrating"),
            "center_wavelength_nm": self._get_float("GetWavelength"),
        }
        if self.capabilities.filter_wheel_present:
            state["filter_position"] = self._get_int("GetFilter")
        state["flipper_positions"] = {
            str(index): self._get_int("GetFlipperMirror", index)
            for index in self.capabilities.flipper_mirrors
        }
        if self.capabilities.focus_mirror_present:
            state["focus_mirror_position"] = self._get_int("GetFocusMirror")
        return state

    def set_grating(self, grating: int) -> None:
        valid = {item.index for item in self.capabilities.gratings}
        if int(grating) not in valid:
            raise ValueError(
                f"Unknown Kymera grating {grating}; valid values are {sorted(valid)}."
            )
        self._call(
            "SetGrating",
            ctypes.c_int(self.device_index),
            ctypes.c_int(grating),
            argtypes=[ctypes.c_int, ctypes.c_int],
        )

    def set_wavelength_nm(self, wavelength_nm: float) -> None:
        value = float(wavelength_nm)
        if not math.isfinite(value):
            raise ValueError("Kymera center wavelength must be finite.")
        current_grating = self._get_int("GetGrating")
        grating = next(
            (
                item
                for item in self.capabilities.gratings
                if item.index == current_grating
            ),
            None,
        )
        if (
            grating is not None
            and not grating.minimum_nm <= value <= grating.maximum_nm
        ):
            raise ValueError(
                f"Kymera wavelength {value:g} nm is outside grating "
                f"{current_grating}'s range [{grating.minimum_nm:g}, "
                f"{grating.maximum_nm:g}] nm."
            )
        self._call(
            "SetWavelength",
            ctypes.c_int(self.device_index),
            ctypes.c_float(value),
            argtypes=[ctypes.c_int, ctypes.c_float],
        )

    def set_filter_position(self, position: int) -> None:
        if int(position) not in self.capabilities.filter_positions:
            raise ValueError("The requested Kymera filter position is unavailable.")
        self._call(
            "SetFilter",
            ctypes.c_int(self.device_index),
            ctypes.c_int(position),
            argtypes=[ctypes.c_int, ctypes.c_int],
        )

    def set_flipper_position(self, flipper: int, position: int) -> None:
        if int(flipper) not in self.capabilities.flipper_mirrors:
            raise ValueError(f"Kymera flipper {flipper} is not present.")
        if int(position) not in self.capabilities.flipper_mirrors[int(flipper)]:
            raise ValueError(
                f"Invalid position {position} for Kymera flipper {flipper}."
            )
        self._call(
            "SetFlipperMirror",
            ctypes.c_int(self.device_index),
            ctypes.c_int(flipper),
            ctypes.c_int(position),
            argtypes=[ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )

    def set_focus_mirror_position(self, position: int) -> None:
        if not self.capabilities.focus_mirror_present:
            raise ValueError("This Kymera does not report a focus mirror.")
        value = int(position)
        maximum = int(self.capabilities.focus_mirror_max_steps)
        if value < 0 or (maximum > 0 and value > maximum):
            raise ValueError(f"Focus position {value} is outside 0..{maximum}.")
        self._call(
            "SetFocusMirror",
            ctypes.c_int(self.device_index),
            ctypes.c_int(value),
            argtypes=[ctypes.c_int, ctypes.c_int],
        )

    def set_grating_offset(self, grating: int, offset: int) -> None:
        valid = {item.index for item in self.capabilities.gratings}
        if int(grating) not in valid:
            raise ValueError(f"Unknown Kymera grating {grating}.")
        self._call(
            "SetGratingOffset",
            ctypes.c_int(self.device_index),
            ctypes.c_int(grating),
            ctypes.c_int(offset),
            argtypes=[ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )

    def set_detector_offset(
        self,
        entrance_port: int,
        exit_port: int,
        offset: int,
    ) -> None:
        if int(entrance_port) not in {0, 1} or int(exit_port) not in {0, 1}:
            raise ValueError("Kymera detector-offset ports must be 0 or 1.")
        self._call(
            "SetDetectorOffset",
            ctypes.c_int(self.device_index),
            ctypes.c_int(entrance_port),
            ctypes.c_int(exit_port),
            ctypes.c_int(offset),
            argtypes=[ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )

    def configure_detector_geometry(
        self,
        *,
        pixel_count: int,
        pixel_width_um: float,
    ) -> None:
        if (
            int(pixel_count) < 1
            or not math.isfinite(pixel_width_um)
            or pixel_width_um <= 0
        ):
            raise ValueError(
                f"Invalid detector geometry: {pixel_count} pixels at {pixel_width_um} µm."
            )
        self._call(
            "SetNumberPixels",
            ctypes.c_int(self.device_index),
            ctypes.c_int(pixel_count),
            argtypes=[ctypes.c_int, ctypes.c_int],
        )
        self._call(
            "SetPixelWidth",
            ctypes.c_int(self.device_index),
            ctypes.c_float(pixel_width_um),
            argtypes=[ctypes.c_int, ctypes.c_float],
        )

    def calibration_nm(self, pixel_count: int) -> np.ndarray:
        count = int(pixel_count)
        if count < 1:
            raise ValueError(
                "A positive detector pixel count is required for calibration."
            )
        values = (ctypes.c_float * count)()
        self._call(
            "GetCalibration",
            ctypes.c_int(self.device_index),
            values,
            ctypes.c_int(count),
            argtypes=[ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int],
        )
        calibration = np.ctypeslib.as_array(values).astype(float, copy=True)
        if calibration.shape != (count,) or not np.all(np.isfinite(calibration)):
            raise AndorSpectrographError(
                "Kymera returned an invalid wavelength calibration."
            )
        if count > 1 and float(np.ptp(calibration)) <= 0.0:
            raise AndorSpectrographError(
                "Kymera returned a constant wavelength calibration."
            )
        return calibration

    def close(self) -> None:
        if self._initialized:
            try:
                self._call("Close", argtypes=[])
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
        try:
            self.close()
        except Exception:
            pass
