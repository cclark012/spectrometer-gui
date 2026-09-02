from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from pathlib import Path

import pylablib as pll
from pylablib.devices import Andor


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    print("=== PyLabLib loaded-Dll diagnostic ===")
    print(f"Python: {sys.version}")
    print(f"Executable: {sys.executable}")
    print(f"Architecture: {8 * ctypes.sizeof(ctypes.c_void_p)}-bit")
    print()

    solis = Path(r"C:\Program Files\Andor SOLIS")

    print("SDK2 DLL Candidates:")
    for name in ("atmcd64d.dll", "atmcd64d_legacy.dll", "atmcd64d_sdk3.dll"):
        path = solis / name
        if path.exists():
            print(
                f"  {name}: "
                f"{path.stat().st_size:,} bytes, "
                f"sha256={sha256(path)}"
            )
        else:
            print(f"  {name}: MISSING")
        print()
        print("Loading camera with PyLabLib...")

        with Andor.AndorSDK2Camera() as cam:
            print("Connected.")
            print("Camera:", cam.get_device_info())
            print("Detector:", cam.get_detector_size())
            print("Pixel Size:", cam.get_pixel_size())

            print()
            print("Loaded DLLs visible to Windows:")

            process = ctypes.windll.kernel32.GetCurrentProcess()

            psapi = ctypes.WinDLL("Psapi.dll")

            GetModuleFileNameExW = psapi.GetModuleFileNameExW
            GetModuleFileNameExW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_ulong,
                ]
            GetModuleFileNameExW.restype = ctypes.c_ulong

            EnumProcessModules = psapi.EnumProcessModules
            EnumProcessModules.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong),
                ]
            EnumProcessModules.restype = ctypes.c_int

            modules = (ctypes.c_void_p * 1024)()
            needed = ctypes.c_ulong()

            ok = EnumProcessModules(
                process,
                modules,
                ctypes.sizeof(modules),
                ctypes.byref(needed),
                )

            if not ok:
                print("  Could not enumerate process modules.")
            else:
                count = needed.value // ctypes.sizeof(ctypes.c_void_p)

                for index in range(count):
                    buffer = ctypes.create_unicode_buffer(1024)

                    result = GetModuleFileNameExW(
                        process,
                        modules[index],
                        buffer,
                        len(buffer),
                        )

                    if not result:
                        continue

                    path = Path(buffer.value)

                    if "atmcd" in path.name.lower() or "andor" in path.name.lower():
                        print(" ", path)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
                    
