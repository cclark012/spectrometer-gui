from __future__ import annotations

import os
import sys
from pathlib import Path


DLL_PATH = Path(
    r"C:\Program Files\Newport\Newport Power Meter Application\Samples\PowerMeterCommands.dll"
)


def main() -> int:
    if not DLL_PATH.exists():
        print(f"Newport DLL not found: {DLL_PATH}", file=sys.stderr)
        return 2

    try:
        from pythonnet import load
    except ImportError:
        print("pythonnet is required for this Newport probe.", file=sys.stderr)
        return 2

    try:
        load("netfx")
    except RuntimeError:
        # Runtime may already be loaded in an interactive session.
        pass

    import clr  # noqa: F401
    from System.Reflection import Assembly

    dll_dir = str(DLL_PATH.parent)
    if dll_dir not in sys.path:
        sys.path.insert(0, dll_dir)

    dll_handle = None
    if hasattr(os, "add_dll_directory"):
        dll_handle = os.add_dll_directory(dll_dir)

    try:
        assembly = Assembly.LoadFrom(str(DLL_PATH))
        print("Loaded:")
        print(assembly.FullName)
        print("Types containing PowerMeter or Command:")
        for item in assembly.GetTypes():
            name = str(item.FullName)
            if "PowerMeter" in name or "Command" in name:
                print(" ", name)
    finally:
        if dll_handle is not None:
            dll_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
