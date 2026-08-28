# newport_2936r_dotnet.py

from __future__ import annotations

import os
import sys
from pathlib import Path

from pythonnet import load

from core.records import PowerSnapshot

try:
    load("netfx")
except RuntimeError:
    # Runtime may already be loaded in an interactive session.
    pass

from System import Array, Double, GC, Int32, Object, Single  # pyright: ignore[reportMissingImports]
from System.Reflection import Assembly  # pyright: ignore[reportMissingImports]


class NewportError(RuntimeError):
    pass


class Newport2936R:
    """
    Minimal Newport 2936-R USB/.NET adapter using PowerMeterCommands.dll.

    Confirmed working constructor:
        PowerMeterCommands(Boolean logging, ref String deviceKey)

    Confirmed useful methods:
        Query(deviceKey, cmd)
        CmdSetRun(deviceKey, run)
        CmdSetUnits(deviceKey, units)
        SetChannel(deviceKey, channel)
        CmdGetPower(deviceKey, ref power)
        CmdGetPowerWithStatus(deviceKey, ref powerArray, ref statusArray)
    """

    UNITS_WATTS = 2

    def __init__(
        self,
        dll_path: str | Path,
        *,
        channel: int = 1,
        units: int = UNITS_WATTS,
        logging: bool = False,
        configure: bool = True,
    ) -> None:
        self.dll_path = Path(dll_path)
        self.channel = int(channel)
        self.units = int(units)
        self.logging = bool(logging)

        self.assembly = None
        self.obj = None
        self.device_key = ""
        self.n_channels = 0
        self._dll_directory_handle = None

        try:
            self._load_assembly()
            self._construct_device()

            self.n_channels = self.get_num_channels()

            if configure:
                self.configure(channel=self.channel, units=self.units)
        except Exception:
            # Constructor/configuration failures can otherwise leave the vendor
            # wrapper holding the USB endpoint, preventing the next hot-plug
            # attempt from opening a fresh device instance.
            self.close()
            raise

    def _load_assembly(self) -> None:
        if not self.dll_path.exists():
            raise FileNotFoundError(f"PowerMeterCommands.dll not found: {self.dll_path}")

        dll_dir = str(self.dll_path.parent)

        if dll_dir not in sys.path:
            sys.path.insert(0, dll_dir)

        if hasattr(os, "add_dll_directory"):
            self._dll_directory_handle = os.add_dll_directory(dll_dir)

        self.assembly = Assembly.LoadFrom(str(self.dll_path))

    def _construct_device(self) -> None:
        target_type = self.assembly.GetType("Newport.PowerMeterCommands.PowerMeterCommands")

        if target_type is None:
            raise NewportError("Could not find Newport.PowerMeterCommands.PowerMeterCommands")

        target_ctor = None

        for ctor in target_type.GetConstructors():
            params = list(ctor.GetParameters())

            if len(params) != 2:
                continue

            p0 = params[0].ParameterType
            p1 = params[1].ParameterType

            is_bool = p0.FullName == "System.Boolean"
            is_ref_string = (
                p1.IsByRef
                and p1.GetElementType() is not None
                and p1.GetElementType().FullName == "System.String"
            )

            if is_bool and is_ref_string:
                target_ctor = ctor
                break

        if target_ctor is None:
            raise NewportError(
                "Could not find constructor "
                "PowerMeterCommands(Boolean logging, ref String deviceKey)"
            )

        args = Array[Object]([self.logging, ""])
        self.obj = target_ctor.Invoke(args)

        self.device_key = str(args[1]).strip() if args[1] is not None else ""

        if not self.device_key:
            raise NewportError(
                "Newport DLL returned an empty device key. "
                "Close Newport applications, check USB connection, "
                "and verify the Newport app can see the meter."
            )

    def _method_signature(self, method) -> str:
        parts = []

        for p in method.GetParameters():
            t = p.ParameterType
            mode = "out " if p.IsOut else "ref " if t.IsByRef else ""

            if t.IsByRef:
                t_name = t.GetElementType().FullName + "&"
            else:
                t_name = t.FullName

            parts.append(f"{mode}{t_name} {p.Name}")

        return f"{method.ReturnType.FullName} {method.Name}(" + ", ".join(parts) + ")"

    def _invoke(
        self,
        name: str,
        values: list[object],
        n_params: int | None = None,
    ) -> tuple[object, list[object], str]:
        methods = []

        for method in self.obj.GetType().GetMethods():
            if not method.IsPublic:
                continue
            if method.Name != name:
                continue
            if n_params is not None and len(method.GetParameters()) != n_params:
                continue
            if n_params is None and len(method.GetParameters()) != len(values):
                continue

            methods.append(method)

        errors = []

        for method in methods:
            arr = Array[Object](values)

            try:
                result = method.Invoke(self.obj, arr)
                return result, list(arr), self._method_signature(method)

            except Exception as exc:
                errors.append(f"{self._method_signature(method)}\n{type(exc).__name__}: {exc}")

        raise NewportError(
            f"All overloads failed for {name} with values={values!r}:\n"
            + "\n\n".join(errors)
        )

    def _check_command_status(self, status: int, call_name: str) -> None:
        if int(status) == 0:
            return

        err_no = None
        err_str = None

        try:
            err_no = self.get_error_number()
        except Exception:
            pass

        try:
            err_str = self.get_error_string()
        except Exception:
            pass

        raise NewportError(
            f"{call_name} failed with command_status={status}, "
            f"error_number={err_no}, error_string={err_str!r}"
        )

    def query(self, command: str) -> str:
        result, _, _ = self._invoke(
            "Query",
            [self.device_key, str(command)],
            n_params=2,
        )

        return "" if result is None else str(result).strip()

    def write(self, command: str) -> int:
        result, _, _ = self._invoke(
            "Write",
            [self.device_key, str(command)],
            n_params=2,
        )

        status = int(result)
        self._check_command_status(status, f"Write({command!r})")
        return status

    def identify(self) -> str:
        return self.query("*IDN?")

    def get_num_channels(self) -> int:
        result, _, _ = self._invoke(
            "GetNumChannels",
            [self.device_key],
            n_params=1,
        )

        return int(result)

    def set_channel(self, channel: int) -> None:
        channel = int(channel)

        if channel < 1:
            raise ValueError("channel must be 1 or greater")
        if self.n_channels and channel > self.n_channels:
            raise ValueError(
                f"channel {channel} exceeds available channel count {self.n_channels}"
            )

        self._invoke(
            "SetChannel",
            [self.device_key, str(channel)],
            n_params=2,
        )

        self.channel = channel

    def set_run(self, run: bool = True) -> int:
        result, _, _ = self._invoke(
            "CmdSetRun",
            [self.device_key, Int32(1 if run else 0)],
            n_params=2,
        )

        status = int(result)
        self._check_command_status(status, "CmdSetRun")
        return status

    def get_run(self) -> int:
        result, final_args, _ = self._invoke(
            "CmdGetRun",
            [self.device_key, Int32(0)],
            n_params=2,
        )

        status = int(result)
        self._check_command_status(status, "CmdGetRun")
        return int(final_args[1])

    def set_units(self, units: int = UNITS_WATTS) -> int:
        result, _, _ = self._invoke(
            "CmdSetUnits",
            [self.device_key, Int32(int(units))],
            n_params=2,
        )

        status = int(result)
        self._check_command_status(status, "CmdSetUnits")
        self.units = int(units)
        return status

    def get_units(self) -> int:
        result, final_args, _ = self._invoke(
            "CmdGetUnits",
            [self.device_key, Int32(0)],
            n_params=2,
        )

        status = int(result)
        self._check_command_status(status, "CmdGetUnits")
        return int(final_args[1])

    def configure(self, *, channel: int = 1, units: int = UNITS_WATTS) -> None:
        self.set_run(True)
        self.set_units(units)
        self.set_channel(channel)

    def read_active_power_watts(self) -> float:
        """
        Reads the currently selected channel using CmdGetPower.
        Use set_channel(1) or set_channel(2) first.
        """

        result, final_args, _ = self._invoke(
            "CmdGetPower",
            [self.device_key, Single(0.0)],
            n_params=2,
        )

        status = int(result)
        self._check_command_status(status, "CmdGetPower")

        return float(final_args[1])

    def read_all_power_with_status(self) -> PowerSnapshot:
        """
        Reads all channels using CmdGetPowerWithStatus.

        command_status:
            Status of the DLL/device command itself.

        pm_status:
            Per-channel power-meter status words. Store these with your data.
            Do not treat nonzero values as Python/.NET communication failures.
        """

        n = max(1, int(self.n_channels))

        powers = Array[Double]([0.0] * n)
        statuses = Array[Int32]([0] * n)

        result, final_args, _ = self._invoke(
            "CmdGetPowerWithStatus",
            [self.device_key, powers, statuses],
            n_params=3,
        )

        command_status = int(result)
        self._check_command_status(command_status, "CmdGetPowerWithStatus")

        powers_w = [float(x) for x in final_args[1]]
        pm_status = [int(x) for x in final_args[2]]

        return PowerSnapshot(
            powers_w=powers_w,
            pm_status=pm_status,
            command_status=command_status,
        )

    def get_power_strings(self) -> list[str]:
        result, _, _ = self._invoke(
            "GetPower",
            [self.device_key],
            n_params=1,
        )

        if result is None:
            return []

        return [str(x) for x in result]

    def get_error_number(self) -> int:
        _, final_args, _ = self._invoke(
            "CmdGetErrorNumber",
            [self.device_key, Int32(0)],
            n_params=2,
        )

        return int(final_args[1])

    def get_error_string(self) -> str:
        _, final_args, _ = self._invoke(
            "CmdGetErrorString",
            [self.device_key, ""],
            n_params=2,
        )

        return "" if final_args[1] is None else str(final_args[1]).strip()

    def set_wavelength_nm(self, wavelength_nm: int) -> int:
        wavelength_nm = int(round(wavelength_nm))

        result, _, _ = self._invoke(
            "CmdSetWavelength",
            [self.device_key, Int32(wavelength_nm)],
            n_params=2,
        )

        status = int(result)
        self._check_command_status(status, "CmdSetWavelength")
        return status

    def get_wavelength_nm(self) -> int:
        result, final_args, _ = self._invoke(
            "CmdGetWavelength",
            [self.device_key, Int32(0)],
            n_params=2,
        )

        status = int(result)
        self._check_command_status(status, "CmdGetWavelength")
        return int(final_args[1])

    def get_min_wavelength_nm(self) -> int:
        # Generic ASCII command path is useful because your DLL already supports Query.
        text = self.query("PM:MIN:Lambda?")
        return int(float(text.strip()))

    def get_max_wavelength_nm(self) -> int:
        text = self.query("PM:MAX:Lambda?")
        return int(float(text.strip()))

    def set_wavelength_for_laser_nm(self, wavelength_nm: float) -> int:
        wavelength_int = int(round(float(wavelength_nm)))

        min_nm = self.get_min_wavelength_nm()
        max_nm = self.get_max_wavelength_nm()

        if wavelength_int < min_nm or wavelength_int > max_nm:
            raise NewportError(
                f"Requested wavelength {wavelength_int} nm is outside detector "
                f"calibrated range [{min_nm}, {max_nm}] nm"
            )

        return self.set_wavelength_nm(wavelength_int)

    def close(self) -> None:
        # DLL revisions differ: some expose Dispose, some Close, and some only
        # release the USB handle when the managed object is finalized.
        if self.obj is not None:
            if self.device_key:
                try:
                    self.set_run(False)
                except Exception:
                    pass
            for method_name in ("Dispose", "Close"):
                try:
                    self._invoke(method_name, [], n_params=0)
                    break
                except Exception:
                    continue
        self.obj = None
        self.assembly = None
        self.device_key = ""
        self.n_channels = 0
        try:
            GC.Collect()
            GC.WaitForPendingFinalizers()
            GC.Collect()
        except Exception:
            pass
        if self._dll_directory_handle is not None:
            try:
                self._dll_directory_handle.close()
            finally:
                self._dll_directory_handle = None

    def __enter__(self) -> Newport2936R:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
