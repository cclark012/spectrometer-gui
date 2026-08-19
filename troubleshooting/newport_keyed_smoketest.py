# newport_keyed_smoketest.py

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from pythonnet import load

try:
    load("netfx")
except RuntimeError:
    pass

import System
from System import Array, Object, Int32, Single, Double
from System.Reflection import Assembly


DLL_PATH = Path(
    r"C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll"
)

TARGET_TYPE = "Newport.PowerMeterCommands.PowerMeterCommands"


def add_dll_search_path(dll_path: Path) -> None:
    dll_dir = str(dll_path.parent)

    if dll_dir not in sys.path:
        sys.path.insert(0, dll_dir)

    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(dll_dir)


def type_name(t) -> str:
    if t.IsByRef:
        return type_name(t.GetElementType()) + "&"
    if t.IsArray:
        return type_name(t.GetElementType()) + "[]"
    return t.FullName


def method_signature(m) -> str:
    params = []
    for p in m.GetParameters():
        mode = "out " if p.IsOut else "ref " if p.ParameterType.IsByRef else ""
        params.append(f"{mode}{type_name(p.ParameterType)} {p.Name}")

    return f"{m.ReturnType.FullName} {m.Name}(" + ", ".join(params) + ")"


def constructor_signature(c) -> str:
    params = []
    for p in c.GetParameters():
        mode = "out " if p.IsOut else "ref " if p.ParameterType.IsByRef else ""
        params.append(f"{mode}{type_name(p.ParameterType)} {p.Name}")

    return ".ctor(" + ", ".join(params) + ")"


def unwrap_exception(exc: Exception) -> str:
    parts = [f"{type(exc).__name__}: {exc}"]

    inner = getattr(exc, "InnerException", None)
    if inner is not None:
        parts.append(f"InnerException: {inner.GetType().FullName}: {inner.Message}")

    return "\n".join(parts)


def invoke_method(obj, name: str, values: list[object], n_params: int | None = None):
    methods = []

    for m in obj.GetType().GetMethods():
        if not m.IsPublic:
            continue
        if m.Name != name:
            continue
        if n_params is not None and len(m.GetParameters()) != n_params:
            continue
        if n_params is None and len(m.GetParameters()) != len(values):
            continue

        methods.append(m)

    errors = []

    for m in methods:
        arr = Array[Object](values)

        try:
            result = m.Invoke(obj, arr)
            return result, list(arr), method_signature(m)
        except Exception as exc:
            errors.append((method_signature(m), unwrap_exception(exc)))

    raise RuntimeError(
        f"All overloads failed for {name} with values={values!r}:\n"
        + "\n\n".join(f"{sig}\n{err}" for sig, err in errors)
    )


def construct_with_device_key(assembly):
    t = assembly.GetType(TARGET_TYPE)

    if t is None:
        raise RuntimeError(f"Could not find type: {TARGET_TYPE}")

    print("Using type:")
    print(" ", t.FullName)

    print()
    print("Constructors:")
    for c in t.GetConstructors():
        print(" ", constructor_signature(c))

    target_ctor = None

    for c in t.GetConstructors():
        params = list(c.GetParameters())

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
            target_ctor = c
            break

    if target_ctor is None:
        raise RuntimeError(
            "Could not find .ctor(System.Boolean logging, ref System.String deviceKey)."
        )

    print()
    print("Forcing constructor:")
    print(" ", constructor_signature(target_ctor))

    args = Array[Object]([False, ""])

    try:
        obj = target_ctor.Invoke(args)
    except Exception as exc:
        raise RuntimeError("Device-key constructor failed:\n" + unwrap_exception(exc)) from exc

    final_args = list(args)
    device_key = str(final_args[1]).strip() if final_args[1] is not None else ""

    print("Constructor final args:")
    print(" ", final_args)
    print("Device key:")
    print(" ", repr(device_key))

    if not device_key:
        raise RuntimeError(
            "The correct constructor ran, but returned an empty device key. "
            "Close the Newport Power Meter Application/ConnectionTest, check USB connection, "
            "and confirm the Newport app can see the meter before Python opens it."
        )

    return obj, device_key


def query(obj, device_key: str, command: str) -> str:
    result, final_args, sig = invoke_method(
        obj,
        "Query",
        [device_key, command],
        n_params=2,
    )

    text = "" if result is None else str(result).strip()

    print()
    print(f"Query: {command}")
    print(" Signature:", sig)
    print(" Return   :", repr(text))

    return text


def write(obj, device_key: str, command: str) -> int:
    result, final_args, sig = invoke_method(
        obj,
        "Write",
        [device_key, command],
        n_params=2,
    )

    status = int(result)

    print()
    print(f"Write: {command}")
    print(" Signature:", sig)
    print(" Status   :", status)

    return status


def set_channel(obj, device_key: str, channel: int) -> None:
    result, final_args, sig = invoke_method(
        obj,
        "SetChannel",
        [device_key, str(channel)],
        n_params=2,
    )

    print()
    print(f"SetChannel: {channel}")
    print(" Signature:", sig)


def cmd_set_int(obj, device_key: str, method_name: str, value: int) -> int:
    result, final_args, sig = invoke_method(
        obj,
        method_name,
        [device_key, Int32(value)],
        n_params=2,
    )

    status = int(result)

    print()
    print(f"{method_name}: {value}")
    print(" Signature:", sig)
    print(" Status   :", status)

    return status


def cmd_get_int(obj, device_key: str, method_name: str) -> tuple[int, int]:
    result, final_args, sig = invoke_method(
        obj,
        method_name,
        [device_key, Int32(0)],
        n_params=2,
    )

    status = int(result)
    value = int(final_args[1])

    print()
    print(method_name)
    print(" Signature:", sig)
    print(" Status   :", status)
    print(" Value    :", value)

    return status, value


def cmd_get_power(obj, device_key: str) -> tuple[int, float]:
    result, final_args, sig = invoke_method(
        obj,
        "CmdGetPower",
        [device_key, Single(0.0)],
        n_params=2,
    )

    status = int(result)
    power = float(final_args[1])

    print()
    print("CmdGetPower")
    print(" Signature:", sig)
    print(" Status   :", status)
    print(" Power    :", f"{power:.12e}")

    return status, power


def cmd_get_power_with_status(
    obj,
    device_key: str,
    n_channels: int,
) -> tuple[int, list[float], list[int]]:
    n = max(1, int(n_channels))

    powers = Array[Double]([0.0] * n)
    statuses = Array[Int32]([0] * n)

    result, final_args, sig = invoke_method(
        obj,
        "CmdGetPowerWithStatus",
        [device_key, powers, statuses],
        n_params=3,
    )

    status = int(result)
    power_list = [float(x) for x in final_args[1]]
    status_list = [int(x) for x in final_args[2]]

    print()
    print("CmdGetPowerWithStatus")
    print(" Signature:", sig)
    print(" Status   :", status)
    print(" Powers   :", power_list)
    print(" PM status:", status_list)

    return status, power_list, status_list


def get_power_string_array(obj, device_key: str) -> list[str]:
    result, final_args, sig = invoke_method(
        obj,
        "GetPower",
        [device_key],
        n_params=1,
    )

    values = [str(x) for x in result] if result is not None else []

    print()
    print("GetPower")
    print(" Signature:", sig)
    print(" Values   :", values)

    return values


def get_num_channels(obj, device_key: str) -> int:
    result, final_args, sig = invoke_method(
        obj,
        "GetNumChannels",
        [device_key],
        n_params=1,
    )

    n = int(result)

    print()
    print("GetNumChannels")
    print(" Signature:", sig)
    print(" Channels :", n)

    return n


def close_if_possible(obj) -> None:
    for name in ["Dispose", "Close"]:
        try:
            invoke_method(obj, name, [], n_params=0)
            print()
            print("Cleanup:", name)
            return
        except Exception:
            pass


def main() -> int:
    if not DLL_PATH.exists():
        print("DLL path does not exist:")
        print(" ", DLL_PATH)
        print("Edit DLL_PATH at the top of this script.")
        return 2

    add_dll_search_path(DLL_PATH)

    print("Loading DLL:")
    print(" ", DLL_PATH)

    assembly = Assembly.LoadFrom(str(DLL_PATH))

    print("Assembly:")
    print(" ", assembly.FullName)

    obj = None

    try:
        obj, device_key = construct_with_device_key(assembly)

        n_channels = get_num_channels(obj, device_key)

        # Generic ASCII-command path.
        idn = query(obj, device_key, "*IDN?")
        print()
        print("IDN:", repr(idn))

        # Configure for basic power readout.
        cmd_set_int(obj, device_key, "CmdSetRun", 1)
        cmd_set_int(obj, device_key, "CmdSetUnits", 2)  # 2 = watts
        set_channel(obj, device_key, 1)

        cmd_get_int(obj, device_key, "CmdGetRun")
        cmd_get_int(obj, device_key, "CmdGetUnits")

        # Try all available power-read paths.
        query(obj, device_key, "PM:P?")
        query(obj, device_key, "PM:PWS?")

        get_power_string_array(obj, device_key)
        cmd_get_power(obj, device_key)
        cmd_get_power_with_status(obj, device_key, n_channels)

        print()
        print("=" * 80)
        print("Repeated power readings")

        for i in range(10):
            status, power = cmd_get_power(obj, device_key)
            print(f"{i:04d}, status={status}, power_W={power:.12e}")
            time.sleep(0.5)

    finally:
        if obj is not None:
            close_if_possible(obj)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
