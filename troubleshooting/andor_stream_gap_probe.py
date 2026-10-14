from __future__ import annotations

"""Exercise the GUI-style SDK2 stream path with pauses between requests.

The pause deliberately lets frames and acquisition notifications accumulate.
This reproduces the condition that differs from the tight-loop timing probe.
Block detector light and close Solis before running from the project root::

    python -m troubleshooting.andor_stream_gap_probe \
        --solis-dir "C:\\Program Files\\Andor SOLIS" \
        --camera-dll atmcd64d_legacy.dll --exposure-ms 15 \
        --frames 100 --gap-ms 100 --output andor_stream_gap.json
"""

import argparse
import json
import math
import platform
import statistics
import sys
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from devices.andor_sdk2 import AndorSDK2Camera
from io_utils.atomic import atomic_text_writer


def _summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "mean_ms": statistics.fmean(values),
        "std_ms": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min_ms": min(values),
        "median_ms": statistics.median(values),
        "max_ms": max(values),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test repeated GUI-style Run-Till-Abort reads after allowing SDK2 "
            "frames and notifications to accumulate."
        )
    )
    parser.add_argument(
        "--solis-dir",
        default=r"C:\Program Files\Andor SOLIS",
        help="Directory containing the Andor SDK2 DLLs.",
    )
    parser.add_argument(
        "--camera-dll",
        default="atmcd64d_legacy.dll",
        help="SDK2 camera DLL filename or absolute path.",
    )
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--vertical-shift-index", type=int)
    parser.add_argument("--exposure-ms", type=float, default=15.0)
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument(
        "--gap-ms",
        type=float,
        default=100.0,
        help="Pause between adapter requests; must be non-negative.",
    )
    parser.add_argument(
        "--align-midpoint",
        action="store_true",
        help=(
            "Exercise gated-style selection by requiring the selected frame's "
            "estimated exposure midpoint to be no earlier than each request."
        ),
    )
    parser.add_argument("--output", default="andor_stream_gap.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if sys.platform != "win32":
        print("This diagnostic requires Windows.", file=sys.stderr)
        return 2
    exposure_ms = float(args.exposure_ms)
    gap_ms = float(args.gap_ms)
    frames = int(args.frames)
    if not math.isfinite(exposure_ms) or exposure_ms <= 0.0:
        print("--exposure-ms must be finite and positive.", file=sys.stderr)
        return 2
    if not math.isfinite(gap_ms) or gap_ms < 0.0:
        print("--gap-ms must be finite and non-negative.", file=sys.stderr)
        return 2
    if not 2 <= frames <= 10_000:
        print("--frames must be between 2 and 10000.", file=sys.stderr)
        return 2

    camera = AndorSDK2Camera(
        Path(args.solis_dir),
        camera_index=int(args.camera_index),
        camera_dll=Path(args.camera_dll),
    )
    frame_results: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    try:
        if args.vertical_shift_index is not None:
            camera.apply_settings(
                replace(
                    camera.settings,
                    vertical_speed_index=int(args.vertical_shift_index),
                )
            )
        settings = asdict(camera.settings)
        capabilities = camera.capabilities.as_control_schema()

        for index in range(frames):
            if index:
                time.sleep(gap_ms * 1.0e-3)
            started_s = time.perf_counter()
            try:
                values = camera.acquire(
                    exposure_ms,
                    averages=1,
                    continuous_session="stream_gap_probe",
                    continuous_target_midpoint_s=(
                        started_s if args.align_midpoint else float("nan")
                    ),
                )
            except Exception as exc:
                failure = {
                    "frame_number": index + 1,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                break
            wall_ms = 1000.0 * (time.perf_counter() - started_s)
            timing = camera.last_timing
            stage = dict(camera.last_stage_timing_ms)
            frame_results.append(
                {
                    "frame_number": index + 1,
                    "wall_ms": wall_ms,
                    "driver_frame_index": (
                        int(timing.driver_frame_index) if timing is not None else -1
                    ),
                    "discarded_frames_before": (
                        int(timing.discarded_frames_before)
                        if timing is not None
                        else 0
                    ),
                    "discarded_frames_for_target": (
                        int(timing.discarded_frames_for_target)
                        if timing is not None
                        else 0
                    ),
                    "pending_frames_after_wait": (
                        int(timing.pending_frames_after_wait)
                        if timing is not None
                        else 0
                    ),
                    "stale_wait_notifications": int(
                        stage.get("stale_wait_notifications", 0)
                    ),
                    "exposure_midpoint_after_request_ms": (
                        1000.0 * (timing.midpoint_estimate_s - started_s)
                        if timing is not None
                        and math.isfinite(timing.midpoint_estimate_s)
                        else float("nan")
                    ),
                    "exposure_start_after_request_ms": (
                        1000.0 * (timing.window_started_s - started_s)
                        if timing is not None
                        and math.isfinite(timing.window_started_s)
                        else float("nan")
                    ),
                    "intensity_min": float(np.min(values)),
                    "intensity_max": float(np.max(values)),
                }
            )
    finally:
        try:
            camera.stop_continuous_acquisition()
        finally:
            camera.close()

    wall_values = [float(item["wall_ms"]) for item in frame_results]
    report = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "solis_dir": str(camera.solis_dir),
        "camera_dll": str(camera.dll_path),
        "requested_exposure_ms": exposure_ms,
        "inter_request_gap_ms": gap_ms,
        "align_midpoint": bool(args.align_midpoint),
        "requested_frames": frames,
        "completed_frames": len(frame_results),
        "success": failure is None and len(frame_results) == frames,
        "failure": failure,
        "camera_settings": settings,
        "camera_capabilities": capabilities,
        "request_wall_time": _summary(wall_values),
        "total_discarded_frames": sum(
            int(item["discarded_frames_before"]) for item in frame_results
        ),
        "total_discarded_frames_for_target": sum(
            int(item["discarded_frames_for_target"]) for item in frame_results
        ),
        "total_stale_wait_notifications": sum(
            int(item["stale_wait_notifications"]) for item in frame_results
        ),
        "frames": frame_results,
    }
    output = Path(args.output).expanduser().resolve()
    with atomic_text_writer(output) as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    print(f"Wrote Andor stream-gap report to: {output}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
