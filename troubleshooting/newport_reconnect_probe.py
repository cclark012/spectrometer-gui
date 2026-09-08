from __future__ import annotations

"""Interactive Newport 2936-R power-cycle/reconnect diagnostic.

Run this from the project root with Newport's application closed::

    python -m troubleshooting.newport_reconnect_probe \
      --dll "C:\\Program Files\\Newport\\Newport Power Meter Application\\Samples\\PowerMeterCommands.dll" \
      --output newport_reconnect_probe.json

The script compares a reconnect in the already-loaded CLR against the GUI's new
restartable child-process path.  It never changes wavelength or configuration.
"""

import argparse
import json
import platform
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from io_utils.atomic import atomic_text_writer


def _attempt(factory: Callable[[], object], label: str) -> dict[str, object]:
    started = time.perf_counter()
    meter = None
    try:
        meter = factory()
        identity = str(meter.identify()).strip()
        snapshot = meter.read_all_power_with_status()
        diagnostics_method = getattr(meter, "diagnostics", None)
        diagnostics = diagnostics_method() if callable(diagnostics_method) else {}
        result: dict[str, object] = {
            "label": label,
            "success": True,
            "identity": identity,
            "adapter_diagnostics": diagnostics,
            "powers_w": [float(value) for value in snapshot.powers_w],
            "pm_status": [int(value) for value in snapshot.pm_status],
            "command_status": int(snapshot.command_status),
            "elapsed_s": time.perf_counter() - started,
        }
    except Exception:
        result = {
            "label": label,
            "success": False,
            "elapsed_s": time.perf_counter() - started,
            "error": traceback.format_exc(),
        }
    finally:
        if meter is not None:
            try:
                meter.close()
                result["close_success"] = True
            except Exception:
                result["close_success"] = False
                result["close_error"] = traceback.format_exc()
    return result


def _pnp_snapshot() -> dict[str, object]:
    """Capture matching present Windows PnP devices without changing them."""

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        (
            "Get-PnpDevice -PresentOnly | "
            "Where-Object { $_.FriendlyName -match 'Newport|2936|Power Meter' "
            "-or $_.InstanceId -match 'Newport|2936' } | "
            "Select-Object Status,Class,FriendlyName,InstanceId | "
            "ConvertTo-Json -Depth 3 -Compress"
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15.0,
            check=False,
        )
    except Exception:
        return {"success": False, "error": traceback.format_exc()}
    payload = completed.stdout.strip()
    try:
        devices = json.loads(payload) if payload else []
    except json.JSONDecodeError:
        devices = payload
    return {
        "success": completed.returncode == 0,
        "return_code": int(completed.returncode),
        "devices": devices,
        "stderr": completed.stderr.strip(),
    }


def _wait_for_user(prompt: str) -> None:
    if not sys.stdin.isatty():
        raise RuntimeError("This power-cycle probe requires an interactive terminal.")
    input(prompt + " Press Enter when ready: ")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare same-process and fresh-process Newport reconnects."
    )
    parser.add_argument("--dll", required=True, help="PowerMeterCommands.dll path")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--output", default="newport_reconnect_probe.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dll_path = Path(args.dll).expanduser().resolve()
    report: dict[str, object] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "dll": str(dll_path),
        "channel": int(args.channel),
        "attempts": [],
        "pnp_snapshots": {"before_initial": _pnp_snapshot()},
    }

    from devices.newport_2936r_dotnet import Newport2936R

    direct_factory = lambda: Newport2936R(dll_path, channel=args.channel, units=2)
    initial = _attempt(direct_factory, "initial_same_process")
    report["attempts"].append(initial)
    print(f"Initial connection: {'PASS' if initial['success'] else 'FAIL'}")
    if not initial["success"]:
        print("The initial direct connection failed; power-cycle comparison cannot proceed.")
    else:
        _wait_for_user("Turn the 2936-R OFF and wait until Windows removes it.")
        report["pnp_snapshots"]["powered_off"] = _pnp_snapshot()
        off_attempt = _attempt(direct_factory, "while_powered_off")
        report["attempts"].append(off_attempt)
        print(f"Powered-off check: {'unexpected PASS' if off_attempt['success'] else 'expected fail'}")

        _wait_for_user(
            "Turn the 2936-R ON and wait until it appears in Device Manager/Newport software"
        )
        report["pnp_snapshots"]["after_power_on"] = _pnp_snapshot()
        for index in range(max(1, int(args.attempts))):
            if index:
                time.sleep(max(0.0, float(args.delay)))
            attempt = _attempt(direct_factory, f"same_process_reconnect_{index + 1}")
            report["attempts"].append(attempt)
            print(
                f"Same-process reconnect {index + 1}: "
                f"{'PASS' if attempt['success'] else 'FAIL'}"
            )

        # Import only after the same-process experiment so this comparison uses
        # the same sequence as the GUI's restartable driver boundary.
        from devices.newport_process_proxy import Newport2936RProcess

        isolated_factory = lambda: Newport2936RProcess(
            dll_path,
            channel=args.channel,
            units=2,
        )
        for index in range(max(1, int(args.attempts))):
            if index:
                time.sleep(max(0.0, float(args.delay)))
            attempt = _attempt(isolated_factory, f"fresh_process_reconnect_{index + 1}")
            report["attempts"].append(attempt)
            print(
                f"Fresh-process reconnect {index + 1}: "
                f"{'PASS' if attempt['success'] else 'FAIL'}"
            )

    output = Path(args.output).expanduser().resolve()
    with atomic_text_writer(output) as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    print(f"Report written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
