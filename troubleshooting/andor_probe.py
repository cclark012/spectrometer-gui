from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any


def _candidate_roots() -> list[Path]:
    roots = [
        Path(r"C:\Program Files\Andor SDK"),
        Path(r"C:\Program Files\Andor SOLIS"),
        Path(r"C:\Program Files\Andor Technology"),
        Path(r"C:\Program Files (x86)\Andor SDK"),
        Path(r"C:\Program Files (x86)\Andor SOLIS"),
        Path(r"C:\Program Files (x86)\Andor Technology"),
    ]
    return [root for root in roots if root.exists()]


def _discover_paths(extra_roots: list[Path]) -> dict[str, list[str]]:
    roots = [*extra_roots, *_candidate_roots()]
    python_paths: set[str] = set()
    dll_dirs: set[str] = set()
    files: list[str] = []
    interesting = {
        "atmcd64d.dll",
        "atmcd32d.dll",
        "atspectrograph.dll",
        "shamrockcif.dll",
    }
    for root in roots:
        if not root.exists():
            continue
        # Restrict the search depth/volume by targeting known folder/file names.
        for wrapper in ("pyAndorSDK2", "pyAndorSpectrograph"):
            for path in root.glob(f"**/{wrapper}"):
                if path.is_dir():
                    python_paths.add(str(path.parent))
        for name in interesting:
            for path in root.glob(f"**/{name}"):
                if path.is_file():
                    files.append(str(path))
                    dll_dirs.add(str(path.parent))
    return {
        "roots": [str(root) for root in roots],
        "python_paths": sorted(python_paths),
        "dll_dirs": sorted(dll_dirs),
        "files": sorted(set(files)),
    }


def _prepare_import_environment(discovery: dict[str, list[str]]) -> list[Any]:
    handles: list[Any] = []
    for path in discovery["python_paths"]:
        if path not in sys.path:
            sys.path.insert(0, path)
    if hasattr(os, "add_dll_directory"):
        for path in discovery["dll_dirs"]:
            try:
                handles.append(os.add_dll_directory(path))
            except OSError:
                pass
    return handles


def _public_methods(obj: Any) -> list[str]:
    methods = []
    for name in dir(obj):
        if name.startswith("_"):
            continue
        value = getattr(obj, name, None)
        if callable(value):
            try:
                signature = str(inspect.signature(value))
            except (TypeError, ValueError):
                signature = "(...)"
            methods.append(f"{name}{signature}")
    return sorted(methods)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {"type": type(value).__name__, "attributes": _jsonable(vars(value))}
    return repr(value)


def _safe_call(obj: Any, name: str, *args: Any) -> dict[str, Any]:
    method = getattr(obj, name, None)
    if not callable(method):
        return {"available": False}
    try:
        result = method(*args)
        return {"available": True, "ok": True, "result": _jsonable(result)}
    except Exception as exc:
        return {
            "available": True,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _first_import(candidates: list[tuple[str, str]]) -> tuple[Any, str]:
    errors = []
    for module_name, attribute in candidates:
        try:
            module = importlib.import_module(module_name)
            return getattr(module, attribute), f"{module_name}.{attribute}"
        except Exception as exc:
            errors.append(f"{module_name}.{attribute}: {type(exc).__name__}: {exc}")
    raise ImportError("\n".join(errors))


def probe_camera(init_dir: str, list_methods: bool) -> dict[str, Any]:
    report: dict[str, Any] = {"backend": "pyAndorSDK2"}
    try:
        atmcd_cls, imported_from = _first_import(
            [
                ("pyAndorSDK2", "atmcd"),
                ("pyAndorSDK2.atmcd", "atmcd"),
            ]
        )
        report["import"] = imported_from
    except Exception as exc:
        report["import_error"] = str(exc)
        return report

    sdk = atmcd_cls()
    if list_methods:
        report["methods"] = _public_methods(sdk)
    initialized = False
    try:
        init = _safe_call(sdk, "Initialize", str(init_dir))
        report["Initialize"] = init
        initialized = bool(init.get("ok"))
        if not initialized:
            return report

        available = _safe_call(sdk, "GetAvailableCameras")
        report["GetAvailableCameras"] = available
        count = 1
        result = available.get("result") if available.get("ok") else None
        if isinstance(result, list) and len(result) >= 2:
            try:
                count = int(result[-1])
            except Exception:
                count = 1

        cameras = []
        for index in range(max(1, count)):
            camera: dict[str, Any] = {"index": index}
            handle_call = _safe_call(sdk, "GetCameraHandle", index)
            camera["GetCameraHandle"] = handle_call
            handle_result = handle_call.get("result")
            handle = None
            if isinstance(handle_result, list) and len(handle_result) >= 2:
                handle = handle_result[-1]
                camera["SetCurrentCamera"] = _safe_call(sdk, "SetCurrentCamera", handle)

            for name, args in (
                ("GetCameraSerialNumber", ()),
                ("GetHeadModel", ()),
                ("GetDetector", ()),
                ("GetPixelSize", ()),
                ("GetTemperatureRange", ()),
                ("GetTemperature", ()),
                ("IsCoolerOn", ()),
                ("GetCapabilities", ()),
                ("GetHardwareVersion", ()),
                ("GetSoftwareVersion", ()),
                ("GetNumberADChannels", ()),
                ("GetNumberAmp", ()),
                ("GetNumberPreAmpGains", ()),
                ("GetNumberVSSpeeds", ()),
                ("GetStatus", ()),
                ("IsInternalMechanicalShutter", ()),
                ("GetShutterMinTimes", ()),
                ("GetMaximumBinning", (0, 0)),
            ):
                camera[name] = _safe_call(sdk, name, *args)
            cameras.append(camera)
        report["cameras"] = cameras
    finally:
        if initialized:
            report["ShutDown"] = _safe_call(sdk, "ShutDown")
    return report


def probe_spectrograph(init_dir: str, dll_dir: str | None, list_methods: bool) -> dict[str, Any]:
    report: dict[str, Any] = {"backend": "pyAndorSpectrograph"}
    try:
        spec_cls, imported_from = _first_import(
            [
                ("pyAndorSpectrograph", "ATSpectrograph"),
                ("pyAndorSpectrograph.spectrograph", "ATSpectrograph"),
            ]
        )
        report["import"] = imported_from
    except Exception as exc:
        report["import_error"] = str(exc)
        return report

    try:
        spec = spec_cls(dll_dir) if dll_dir else spec_cls()
    except TypeError:
        spec = spec_cls()
    if list_methods:
        report["methods"] = _public_methods(spec)

    initialized = False
    try:
        init = _safe_call(spec, "Initialize", str(init_dir))
        report["Initialize"] = init
        initialized = bool(init.get("ok"))
        if not initialized:
            return report

        number_call = _safe_call(spec, "GetNumberDevices")
        report["GetNumberDevices"] = number_call
        count = 0
        result = number_call.get("result") if number_call.get("ok") else None
        if isinstance(result, list) and len(result) >= 2:
            try:
                count = int(result[-1])
            except Exception:
                count = 0

        devices = []
        for device_index in range(max(0, count)):
            device: dict[str, Any] = {"index": device_index}
            for name, args in (
                ("GetSerialNumber", (device_index,)),
                ("GetNumberGratings", (device_index,)),
                ("GetGrating", (device_index,)),
                ("GetWavelength", (device_index,)),
                ("GetFocusMirror", (device_index,)),
                ("GetShutter", (device_index,)),
                ("GetDetectorOffset", (device_index,)),
                ("GetNumberPixels", (device_index,)),
                ("GetPixelWidth", (device_index,)),
            ):
                device[name] = _safe_call(spec, name, *args)

            grating_count = 0
            grating_result = device["GetNumberGratings"].get("result")
            if isinstance(grating_result, list) and len(grating_result) >= 2:
                try:
                    grating_count = int(grating_result[-1])
                except Exception:
                    grating_count = 0
            gratings = []
            # Andor grating indices are commonly 1-based.
            for grating in range(1, grating_count + 1):
                gratings.append(
                    {
                        "index": grating,
                        "GetGratingInfo": _safe_call(
                            spec, "GetGratingInfo", device_index, grating
                        ),
                        "GetWavelengthLimits": _safe_call(
                            spec, "GetWavelengthLimits", device_index, grating
                        ),
                    }
                )
            device["gratings"] = gratings

            slits = []
            for slit in range(1, 5):
                slits.append(
                    {
                        "index": slit,
                        "AutoSlitIsPresent": _safe_call(
                            spec, "AutoSlitIsPresent", device_index, slit
                        ),
                        "GetAutoSlitWidth": _safe_call(
                            spec, "GetAutoSlitWidth", device_index, slit
                        ),
                    }
                )
            device["slits"] = slits

            flippers = []
            for flipper in (1, 2):
                flippers.append(
                    {
                        "index": flipper,
                        "FlipperMirrorIsPresent": _safe_call(
                            spec, "FlipperMirrorIsPresent", device_index, flipper
                        ),
                        "GetFlipperMirror": _safe_call(
                            spec, "GetFlipperMirror", device_index, flipper
                        ),
                    }
                )
            device["flippers"] = flippers
            devices.append(device)
        report["devices"] = devices
    finally:
        if initialized:
            close_name = "Close" if callable(getattr(spec, "Close", None)) else "ShutDown"
            report[close_name] = _safe_call(spec, close_name)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only capability probe for Andor SDK2 cameras and Kymera/Shamrock spectrographs." # noqa
    )
    parser.add_argument(
        "--sdk-root",
        action="append",
        default=[],
        help="Additional Andor SDK/Solis installation root. May be repeated.",
    )
    parser.add_argument(
        "--camera-init-dir",
        default="",
        help="Directory passed to SDK2 Initialize(). Empty string uses wrapper defaults.",
    )
    parser.add_argument(
        "--spectrograph-init-dir",
        default="",
        help="Directory passed to ATSpectrograph.Initialize().",
    )
    parser.add_argument(
        "--spectrograph-dll-dir",
        default=None,
        help="Optional directory passed to ATSpectrograph constructor.",
    )
    parser.add_argument("--camera-only", action="store_true")
    parser.add_argument("--spectrograph-only", action="store_true")
    parser.add_argument("--list-methods", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = [Path(value).expanduser().resolve() for value in args.sdk_root]
    discovery = _discover_paths(roots)
    dll_handles = _prepare_import_environment(discovery)
    report: dict[str, Any] = {
        "python": sys.version,
        "architecture": platform.architecture(),
        "platform": platform.platform(),
        "discovery": discovery,
    }
    if not args.spectrograph_only:
        report["camera"] = probe_camera(args.camera_init_dir, args.list_methods)
    if not args.camera_only:
        report["spectrograph"] = probe_spectrograph(
            args.spectrograph_init_dir,
            args.spectrograph_dll_dir,
            args.list_methods,
        )

    # Keep os.add_dll_directory handles alive through all SDK calls.
    report["dll_directory_handles"] = len(dll_handles)
    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    camera_ok = "camera" not in report or "import_error" not in report["camera"]
    spec_ok = "spectrograph" not in report or "import_error" not in report["spectrograph"]
    return 0 if camera_ok or spec_ok else 2


def _format_report(report: dict[str, Any]) -> str:
    lines = [
        f"Python: {report['python'].splitlines()[0]}",
        f"Architecture: {report['architecture']}",
        f"Platform: {report['platform']}",
        "",
        "Discovered wrapper paths:",
    ]
    for path in report["discovery"]["python_paths"]:
        lines.append(f"  {path}")
    lines.append("Discovered DLLs:")
    for path in report["discovery"]["files"]:
        lines.append(f"  {path}")
    for section in ("camera", "spectrograph"):
        if section not in report:
            continue
        lines.append("")
        lines.append(section.upper())
        lines.append(json.dumps(report[section], indent=2))
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
