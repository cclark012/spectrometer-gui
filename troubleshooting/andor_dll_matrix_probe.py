from __future__ import annotations

"""Compare installed Andor SDK2 camera DLLs in isolated Python processes.

Each candidate is delegated to a separate ``andor_ctypes_probe`` process.  This
matters because loading multiple SDK2 runtimes into one process can retain
process-global driver state and make the comparison itself unreliable.

Close Solis first, then run from the project root::

    python -m troubleshooting.andor_dll_matrix_probe \
        --solis-dir "C:\\Program Files\\Andor SOLIS" \
        --output andor_dll_matrix.json
"""

import argparse
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from io_utils.atomic import atomic_text_writer


DEFAULT_CANDIDATES = (
    "atmcd64d_legacy.dll",
    "atmcd64d.dll",
    "atmcd32d_legacy.dll",
    "atmcd32d.dll",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare SDK2 camera DLL enumeration in isolated processes."
    )
    parser.add_argument(
        "--solis-dir",
        default=r"C:\Program Files\Andor SOLIS",
        help="Directory containing the Andor SDK2 DLLs.",
    )
    parser.add_argument(
        "--camera-dll",
        action="append",
        dest="camera_dlls",
        help="Candidate filename/path; repeat to override the default candidate list.",
    )
    parser.add_argument(
        "--output",
        default="andor_dll_matrix.json",
        help="Combined JSON report path.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="Maximum seconds allowed for each isolated probe.",
    )
    return parser.parse_args(argv)


def _camera_count(report: object) -> int:
    try:
        return int(report["camera"]["available_camera_count"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return 0


def _run_candidate(
    *,
    solis_dir: Path,
    candidate: str,
    timeout_s: float,
) -> dict[str, Any]:
    candidate_path = Path(candidate)
    resolved = (
        candidate_path
        if candidate_path.is_absolute()
        else solis_dir / candidate_path
    )
    result: dict[str, Any] = {
        "candidate": str(candidate),
        "resolved_path": str(resolved),
        "exists": resolved.is_file(),
        "return_code": None,
        "camera_count": 0,
        "stderr": "",
        "report": None,
    }
    if not resolved.is_file():
        result["status"] = "not_found"
        return result

    command = [
        sys.executable,
        "-m",
        "troubleshooting.andor_ctypes_probe",
        "--solis-dir",
        str(solis_dir),
        "--camera-dll",
        str(resolved),
        "--camera-only",
        "--json",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5.0, float(timeout_s)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result["status"] = "timeout"
        result["stderr"] = str(exc.stderr or "")
        return result

    result["return_code"] = int(completed.returncode)
    result["stderr"] = completed.stderr.strip()
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result["status"] = "invalid_json"
        result["stdout"] = completed.stdout.strip()
        return result

    count = _camera_count(report)
    result["report"] = report
    result["camera_count"] = count
    result["status"] = "enumerated" if count > 0 else "zero_cameras"
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if sys.platform != "win32":
        print("This diagnostic requires Windows.", file=sys.stderr)
        return 2

    solis_dir = Path(args.solis_dir).expanduser().resolve()
    candidates = tuple(args.camera_dlls or DEFAULT_CANDIDATES)
    results = [
        _run_candidate(
            solis_dir=solis_dir,
            candidate=candidate,
            timeout_s=args.timeout,
        )
        for candidate in candidates
    ]
    report = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "architecture": platform.architecture(),
        "solis_dir": str(solis_dir),
        "results": results,
        "working_candidates": [
            item["resolved_path"]
            for item in results
            if int(item.get("camera_count", 0)) > 0
        ],
    }

    output = Path(args.output).expanduser().resolve()
    with atomic_text_writer(output) as file:
        json.dump(report, file, indent=2)
        file.write("\n")

    print(f"Wrote Andor DLL comparison to: {output}")
    for item in results:
        print(
            f"{item['candidate']}: {item['status']} "
            f"(cameras={item['camera_count']})"
        )
    return 0 if report["working_candidates"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
