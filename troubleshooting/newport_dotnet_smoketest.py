# newport_dotnet_smoketest.py

from __future__ import annotations

import os
import sys
from pathlib import Path

from pythonnet import load

# For Newport's older .NET Framework DLLs, load netfx before importing clr/System.
try:
    load("netfx")
except RuntimeError:
    pass

import clr
import System
from System import Array, Object
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


def short_type_name(t) -> str:
    if t.IsByRef:
        return short_type_name(t.GetElementType()) + "&"
    return t.FullName


def method_signature(m) -> str:
    params = []
    for p in m.GetParameters():
        prefix = "out " if p.IsOut else "ref " if p.ParameterType.IsByRef else ""
        params.append(f"{prefix}{short_type_name(p.ParameterType)} {p.Name}")
    return f"{m.ReturnType.FullName} {m.Name}(" + ", ".join(params) + ")"


def constructor_signature(c) -> str:
    params = []
    for p in c.GetParameters():
        prefix = "out " if p.IsOut else "ref " if p.ParameterType.IsByRef else ""
        params.append(f"{prefix}{short_type_name(p.ParameterType)} {p.Name}")
    return ".ctor(" + ", ".join(params) + ")"


def default_value_for_param(p):
    t = p.ParameterType.GetElementType() if p.ParameterType.IsByRef else p.ParameterType
    name = t.FullName

    if name == "System.Boolean":
        return False
    if name == "System.String":
        return ""
    if name in {
        "System.Byte",
        "System.SByte",
        "System.Int16",
        "System.UInt16",
        "System.Int32",
        "System.UInt32",
        "System.Int64",
        "System.UInt64",
    }:
        return 0
    if name in {"System.Single", "System.Double", "System.Decimal"}:
        return 0.0
    if t.IsEnum:
        return System.Enum.ToObject(t, 0)

    return None


def unwrap_exception(exc: Exception) -> str:
    parts = [f"{type(exc).__name__}: {exc}"]

    inner = getattr(exc, "InnerException", None)
    if inner is not None:
        parts.append(f"InnerException: {inner.GetType().FullName}: {inner.Message}")

    return "\n".join(parts)


def construct_power_meter_commands(asm):
    t = asm.GetType(TARGET_TYPE)

    if t is None:
        candidates = [
            x for x in asm.GetTypes()
            if x.FullName.endswith(".PowerMeterCommands")
        ]

        if not candidates:
            raise RuntimeError("Could not find Newport.PowerMeterCommands.PowerMeterCommands.")

        t = candidates[0]

    print("Using type:")
    print(" ", t.FullName)

    print()
    print("Constructors:")
    for c in t.GetConstructors():
        print(" ", constructor_signature(c))

    errors = []

    for c in t.GetConstructors():
        params = list(c.GetParameters())
        values = [default_value_for_param(p) for p in params]
        arr = Array[Object](values)

        print()
        print("Trying constructor:")
        print(" ", constructor_signature(c))
        print(" Initial args:", list(arr))

        try:
            obj = c.Invoke(arr)
            final_args = list(arr)

            print(" Constructor OK")
            print(" Final args:", final_args)

            device_key = None
            for p, value in zip(params, final_args):
                pname = (p.Name or "").lower()
                if "device" in pname or "key" in pname:
                    if value is not None and str(value).strip():
                        device_key = str(value).strip()

            if device_key is None:
                for value in final_args:
                    if value is not None and isinstance(value, str) and value.strip():
                        device_key = value.strip()
                        break

            print(" Device key:", repr(device_key))
            return obj, device_key

        except Exception as exc:
            msg = unwrap_exception(exc)
            errors.append((constructor_signature(c), msg))
            print(" Constructor failed:")
            print(msg)

    raise RuntimeError("All constructors failed:\n" + "\n\n".join(str(e) for e in errors))


def invoke_reflection(obj, method_name: str, values: list[object]):
    methods = [
        m for m in obj.GetType().GetMethods()
        if m.IsPublic and m.Name.lower() == method_name.lower()
    ]

    errors = []

    for m in methods:
        params = list(m.GetParameters())

        if len(params) != len(values):
            continue

        arr = Array[Object](values)

        try:
            result = m.Invoke(obj, arr)
            return {
                "ok": True,
                "signature": method_signature(m),
                "input_values": values,
                "return": result,
                "final_args": list(arr),
            }

        except Exception as exc:
            errors.append(
                {
                    "signature": method_signature(m),
                    "input_values": values,
                    "error": unwrap_exception(exc),
                }
            )

    return {
        "ok": False,
        "method": method_name,
        "values": values,
        "errors": errors,
    }


def print_result(label: str, result: dict) -> bool:
    print()
    print("=" * 80)
    print(label)

    if result["ok"]:
        print("OK")
        print("Signature:", result["signature"])
        print("Input values:", result["input_values"])
        print("Return:", repr(result["return"]))
        print("Final args:", result["final_args"])
        return True

    print("FAILED")
    print("Method:", result.get("method"))
    print("Values:", result.get("values"))

    for e in result.get("errors", [])[-5:]:
        print("Candidate:", e["signature"])
        print("Input values:", e["input_values"])
        print("Error:", e["error"])

    return False


def query_candidates(obj, device_key: str | None, command: str) -> list[dict]:
    attempts = []

    # Common possibilities:
    # Query(command)
    attempts.append(invoke_reflection(obj, "Query", [command]))

    # Query(deviceKey, command)
    if device_key:
        attempts.append(invoke_reflection(obj, "Query", [device_key, command]))
        attempts.append(invoke_reflection(obj, "Query", [command, device_key]))

    # Query(deviceKey, command, out response)
    if device_key:
        attempts.append(invoke_reflection(obj, "Query", [device_key, command, ""]))
        attempts.append(invoke_reflection(obj, "Query", [command, device_key, ""]))

    # Fallback: no device key known.
    attempts.append(invoke_reflection(obj, "Query", [command, ""]))
    attempts.append(invoke_reflection(obj, "Query", [command, "", ""]))

    return attempts


def extract_response(result: dict, command: str) -> str | None:
    if not result.get("ok"):
        return None

    candidates = []

    ret = result.get("return")
    if ret is not None:
        candidates.append(str(ret))

    for x in result.get("final_args", []):
        if x is not None:
            candidates.append(str(x))

    cleaned = []
    for s in candidates:
        s = s.strip()
        if not s:
            continue
        if s == command:
            continue
        if s not in cleaned:
            cleaned.append(s)

    if not cleaned:
        return None

    return cleaned[-1]


def try_query(obj, device_key: str | None, command: str) -> str | None:
    print()
    print("#" * 80)
    print(f"Trying Query for command: {command}")

    for i, result in enumerate(query_candidates(obj, device_key, command), start=1):
        ok = print_result(f"Query attempt {i}", result)

        if ok:
            response = extract_response(result, command)
            print("Extracted response:", repr(response))
            return response

    return None


def print_focused_methods(obj) -> None:
    keys = [
        "query",
        "read",
        "write",
        "power",
        "chan",
        "unit",
        "run",
        "error",
        "status",
        "wavelength",
        "filter",
    ]

    methods = [
        m for m in obj.GetType().GetMethods()
        if m.IsPublic
        and not m.IsSpecialName
        and any(k in m.Name.lower() for k in keys)
    ]

    print()
    print("Focused public methods:")
    for m in sorted(methods, key=lambda x: x.Name):
        print(" ", method_signature(m))


def try_cmd_get_power(obj, device_key: str | None) -> str | None:
    if not device_key:
        print()
        print("Skipping CmdGetPower: no device key.")
        return None

    print()
    print("#" * 80)
    print("Trying direct CmdGetPower(deviceKey, out power)")

    attempts = [
        [device_key, ""],
        [device_key, 0.0],
    ]

    for i, values in enumerate(attempts, start=1):
        result = invoke_reflection(obj, "CmdGetPower", values)
        ok = print_result(f"CmdGetPower attempt {i}", result)

        if ok:
            # Most Newport command-wrapper methods return an integer status
            # and place the useful value in an out/ref argument.
            for x in reversed(result["final_args"]):
                if x is not None and str(x).strip() and str(x).strip() != device_key:
                    return str(x).strip()

    return None


def main() -> int:
    if not DLL_PATH.exists():
        print("DLL_PATH does not exist:")
        print(DLL_PATH)
        print()
        print("Edit DLL_PATH at the top of this script.")
        return 2

    add_dll_search_path(DLL_PATH)

    print("Loading DLL:")
    print(" ", DLL_PATH)

    asm = Assembly.LoadFrom(str(DLL_PATH))

    print("Assembly:")
    print(" ", asm.FullName)

    obj, device_key = construct_power_meter_commands(asm)

    print_focused_methods(obj)

    # First, use generic command access if available.
    idn = try_query(obj, device_key, "*IDN?")
    print()
    print("IDN result:", repr(idn))

    # Non-destructive read commands.
    p = try_query(obj, device_key, "PM:P?")
    print()
    print("PM:P? result:", repr(p))

    pws = try_query(obj, device_key, "PM:PWS?")
    print()
    print("PM:PWS? result:", repr(pws))

    # Fallback to Newport's direct wrapper method.
    direct_power = try_cmd_get_power(obj, device_key)
    print()
    print("CmdGetPower result:", repr(direct_power))

    # Cleanup if available.
    for cleanup_name in ["Dispose", "Close"]:
        result = invoke_reflection(obj, cleanup_name, [])
        if result["ok"]:
            print()
            print("Cleanup OK:", cleanup_name)
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
