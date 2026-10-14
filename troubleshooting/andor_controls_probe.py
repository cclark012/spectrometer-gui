from __future__ import annotations

"""Query or safely exercise the production iDus/Kymera settings path.

The probe is read-only unless ``--apply`` is supplied. Any requested changes
are restored before shutdown unless ``--keep-changes`` is also supplied. Close
Solis and stop GUI acquisition before running from the project root::

    python -m troubleshooting.andor_controls_probe \
        --solis-dir "C:\\Program Files\\Andor SOLIS" \
        --camera-dll atmcd64d_legacy.dll --output andor_controls.json

To test one reversible change, choose a value that differs from the current
readback and explicitly authorize it::

    python -m troubleshooting.andor_controls_probe \
        --solis-dir "C:\\Program Files\\Andor SOLIS" \
        --camera-dll atmcd64d_legacy.dll --set-center-nm 651 --apply \
        --output andor_controls_651nm.json
"""

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devices.andor_adapter import AndorKymeraSpectrometer
from io_utils.atomic import atomic_text_writer


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Query the production Andor settings path and optionally apply a "
            "readback-verified, automatically restored test change."
        )
    )
    parser.add_argument("--solis-dir", default=r"C:\Program Files\Andor SOLIS")
    parser.add_argument("--camera-dll", default="atmcd64d_legacy.dll")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--spectrograph-index", type=int, default=0)
    parser.add_argument("--set-grating", type=int)
    parser.add_argument("--set-center-nm", type=float)
    parser.add_argument("--set-filter", type=int)
    parser.add_argument("--set-focus", type=int)
    parser.add_argument("--set-flipper-2", type=int, choices=(0, 1))
    parser.add_argument("--set-horizontal-speed-index", type=int)
    parser.add_argument("--set-vertical-speed-index", type=int)
    parser.add_argument("--set-preamp-gain-index", type=int)
    parser.add_argument("--set-horizontal-binning", type=int)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually send requested settings. Without this flag the probe is read-only.",
    )
    parser.add_argument(
        "--keep-changes",
        action="store_true",
        help="Do not restore the original settings after an applied test.",
    )
    parser.add_argument("--output", default="andor_controls.json")
    return parser.parse_args(argv)


def _requested_payload(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    camera_names = {
        "set_horizontal_speed_index": "horizontal_speed_index",
        "set_vertical_speed_index": "vertical_speed_index",
        "set_preamp_gain_index": "preamp_gain_index",
        "set_horizontal_binning": "horizontal_binning",
    }
    spectrograph_names = {
        "set_grating": "grating",
        "set_center_nm": "center_wavelength_nm",
        "set_filter": "filter_position",
        "set_focus": "focus_mirror_position",
    }
    camera = {
        output: getattr(args, source)
        for source, output in camera_names.items()
        if getattr(args, source) is not None
    }
    spectrograph = {
        output: getattr(args, source)
        for source, output in spectrograph_names.items()
        if getattr(args, source) is not None
    }
    if args.set_flipper_2 is not None:
        spectrograph["flipper_positions"] = {"2": int(args.set_flipper_2)}
    return {"camera": camera, "spectrograph": spectrograph}


def _restore_payload(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    camera = dict(state.get("camera", {}))
    spectrograph = dict(state.get("spectrograph", {}))
    return {
        "camera": camera,
        "spectrograph": {
            name: spectrograph[name]
            for name in (
                "grating",
                "center_wavelength_nm",
                "filter_position",
                "flipper_positions",
                "focus_mirror_position",
            )
            if name in spectrograph
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    requested = _requested_payload(args)
    has_changes = any(requested.values())
    if args.keep_changes and not args.apply:
        print("--keep-changes requires --apply.", file=sys.stderr)
        return 2
    if has_changes and not args.apply:
        print(
            "Settings were requested but --apply was omitted; refusing to move hardware.",
            file=sys.stderr,
        )
        return 2
    if args.apply and not has_changes:
        print("--apply requires at least one --set-* option.", file=sys.stderr)
        return 2
    if sys.platform != "win32":
        print("This diagnostic requires Windows.", file=sys.stderr)
        return 2

    adapter: AndorKymeraSpectrometer | None = None
    original_state: dict[str, Any] = {}
    applied_state: dict[str, Any] | None = None
    restored_state: dict[str, Any] | None = None
    control_schema: dict[str, Any] = {}
    connection_error = ""
    apply_error = ""
    restore_error = ""
    try:
        try:
            adapter = AndorKymeraSpectrometer(
                Path(args.solis_dir),
                camera_index=int(args.camera_index),
                spectrograph_index=int(args.spectrograph_index),
                camera_dll=Path(args.camera_dll),
            )
            original_state = adapter.user_state(refresh=True)
            control_schema = dict(adapter.capabilities().control_schema)
        except Exception as exc:
            connection_error = f"{type(exc).__name__}: {exc}"
        if adapter is not None and args.apply:
            try:
                adapter.apply_user_settings(requested)
                applied_state = adapter.user_state(refresh=True)
            except Exception as exc:
                apply_error = f"{type(exc).__name__}: {exc}"
        if adapter is not None and args.apply and not args.keep_changes:
            try:
                adapter.apply_user_settings(_restore_payload(original_state))
                restored_state = adapter.user_state(refresh=True)
            except Exception as exc:
                restore_error = f"{type(exc).__name__}: {exc}"
    finally:
        if adapter is not None:
            adapter.close()

    success = not connection_error and not apply_error and not restore_error
    report = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "solis_dir": str(Path(args.solis_dir)),
        "camera_dll": str(args.camera_dll),
        "read_only": not bool(args.apply),
        "changes_retained": bool(args.apply and args.keep_changes),
        "requested": requested,
        "original_state": original_state,
        "applied_state": applied_state,
        "restored_state": restored_state,
        "control_schema": control_schema,
        "connection_error": connection_error,
        "apply_error": apply_error,
        "restore_error": restore_error,
        "success": success,
        "verification": (
            "Kymera values use native getter readback; SDK2 camera setting "
            "indices are confirmed by successful setter returns and the adapter's "
            "active configuration because SDK2 exposes no matching getters."
        ),
    }
    output = Path(args.output).expanduser().resolve()
    with atomic_text_writer(output) as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    print(f"Wrote Andor controls report to: {output}")
    if connection_error:
        print(f"Connection failed: {connection_error}", file=sys.stderr)
    if apply_error:
        print(f"Settings update failed: {apply_error}", file=sys.stderr)
    if restore_error:
        print(
            "WARNING: original settings could not be restored; inspect the report "
            "and verify the instruments before acquisition.",
            file=sys.stderr,
        )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
