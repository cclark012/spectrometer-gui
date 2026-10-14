from __future__ import annotations

"""Measure SDK2 single-scan overhead and continuous FVB throughput.

This diagnostic acquires camera frames but does not move the Kymera, operate a
laser, or change cooler state. Close Solis and block the detector input before
running it from the project root::

    python -m troubleshooting.andor_timing_probe \
        --solis-dir "C:\\Program Files\\Andor SOLIS" \
        --camera-dll atmcd64d_legacy.dll --frames 20

The continuous comparison uses Run-Till-Abort with internal triggering, which
is the closest SDK2 analogue to a Solis Live acquisition. The adapter settings
are restored before shutdown.
"""

import argparse
import ctypes
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

from devices.andor_sdk2 import AndorSDK2Camera
from io_utils.atomic import atomic_text_writer


def _summary(values: list[float]) -> dict[str, float | int]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return {"count": 0}
    ordered = sorted(finite)

    def percentile(fraction: float) -> float:
        position = fraction * (len(ordered) - 1)
        low = int(math.floor(position))
        high = int(math.ceil(position))
        if low == high:
            return ordered[low]
        weight = position - low
        return ordered[low] * (1.0 - weight) + ordered[high] * weight

    return {
        "count": len(ordered),
        "mean_ms": statistics.fmean(ordered),
        "std_ms": statistics.stdev(ordered) if len(ordered) > 1 else 0.0,
        "min_ms": ordered[0],
        "median_ms": statistics.median(ordered),
        "p95_ms": percentile(0.95),
        "max_ms": ordered[-1],
    }


def _single_scan_benchmark(
    camera: AndorSDK2Camera,
    *,
    exposure_ms: float,
    frames: int,
) -> dict[str, Any]:
    durations_ms: list[float] = []
    sdk_profiles: list[dict[str, Any]] = []
    stage_timings: list[dict[str, Any]] = []
    for _index in range(frames):
        started = time.perf_counter()
        camera.acquire(exposure_ms, averages=1)
        durations_ms.append(1000.0 * (time.perf_counter() - started))
        sdk_profiles.append(asdict(camera.last_acquisition_profile))
        stage_timings.append(dict(camera.last_stage_timing_ms))

    def stage_summary(name: str) -> dict[str, float | int]:
        values: list[float] = []
        for stage in stage_timings:
            value = stage.get(name, ())
            if isinstance(value, (list, tuple)):
                values.extend(float(item) for item in value)
            elif value is not None:
                values.append(float(value))
        return _summary(values)

    return {
        "mode": "single_scan_rearmed_each_frame",
        "requested_exposure_ms": float(exposure_ms),
        "wall_time": _summary(durations_ms),
        "sdk_profile_first": sdk_profiles[0] if sdk_profiles else {},
        "sdk_profile_last": sdk_profiles[-1] if sdk_profiles else {},
        "stage_wall_time": {
            name: stage_summary(name)
            for name in (
                "settings_and_timing_query_ms",
                "prepare_ms",
                "start_command_ms",
                "wait_ms",
                "data_transfer_ms",
            )
        },
        "prepared_state_reuse_count": sum(
            bool(stage.get("prepared_state_reused")) for stage in stage_timings
        ),
    }


def _continuous_fvb_benchmark(
    camera: AndorSDK2Camera,
    *,
    exposure_ms: float,
    frames: int,
) -> dict[str, Any]:
    """Benchmark a single armed Run-Till-Abort stream using oldest-image reads."""

    original_settings = camera.settings
    count = int(camera.output_pixel_count)
    completion_times: list[float] = []
    wait_durations_ms: list[float] = []
    transfer_durations_ms: list[float] = []
    started = float("nan")
    profile: dict[str, Any] = {}
    setup_started = time.perf_counter()
    prepare_duration_ms = float("nan")
    start_command_ms = float("nan")
    try:
        camera._call("SetAcquisitionMode", ctypes.c_int(5), argtypes=[ctypes.c_int])
        camera._call("SetTriggerMode", ctypes.c_int(0), argtypes=[ctypes.c_int])
        camera._call(
            "SetKineticCycleTime",
            ctypes.c_float(0.0),
            argtypes=[ctypes.c_float],
        )
        camera._call(
            "SetExposureTime",
            ctypes.c_float(float(exposure_ms) * 1.0e-3),
            argtypes=[ctypes.c_float],
        )
        current_profile = camera._read_acquisition_profile(
            requested_exposure_ms=float(exposure_ms)
        )
        profile = asdict(current_profile)
        prepare_started = time.perf_counter()
        camera._call("PrepareAcquisition", argtypes=[])
        prepare_finished = time.perf_counter()
        prepare_duration_ms = 1000.0 * (prepare_finished - prepare_started)
        started = time.perf_counter()
        camera._call("StartAcquisition", argtypes=[])
        start_finished = time.perf_counter()
        start_command_ms = 1000.0 * (start_finished - started)
        timeout_ms = max(
            10_000,
            int(
                math.ceil(
                    2.0
                    * max(
                        float(exposure_ms),
                        float(current_profile.kinetic_cycle_ms),
                        float(current_profile.readout_ms),
                    )
                    + 10_000.0
                )
            ),
        )
        for _index in range(frames):
            wait_started = time.perf_counter()
            camera._call(
                "WaitForAcquisitionTimeOut",
                ctypes.c_int(timeout_ms),
                argtypes=[ctypes.c_int],
            )
            completed = time.perf_counter()
            wait_durations_ms.append(1000.0 * (completed - wait_started))
            completion_times.append(completed)
            transfer_started = time.perf_counter()
            buffer = (ctypes.c_int32 * count)()
            camera._call(
                "GetOldestImage",
                buffer,
                ctypes.c_ulong(count),
                argtypes=[ctypes.POINTER(ctypes.c_int32), ctypes.c_ulong],
            )
            transfer_durations_ms.append(
                1000.0 * (time.perf_counter() - transfer_started)
            )
    finally:
        try:
            camera._call("AbortAcquisition", argtypes=[])
        except Exception:
            pass
        camera.apply_settings(original_settings)

    completion_periods_ms = [
        1000.0 * (later - earlier)
        for earlier, later in zip(completion_times, completion_times[1:])
    ]
    total_s = completion_times[-1] - started if completion_times else float("nan")
    return {
        "mode": "run_till_abort_internal_fvb",
        "requested_exposure_ms": float(exposure_ms),
        "sdk_profile": profile,
        "stage_wall_time": {
            "settings_and_timing_query_ms": 1000.0
            * (prepare_started - setup_started),
            "prepare_ms": prepare_duration_ms,
            "start_command_ms": start_command_ms,
            "wait_ms": _summary(wait_durations_ms),
            "data_transfer_ms": _summary(transfer_durations_ms),
        },
        "first_frame_latency_ms": (
            1000.0 * (completion_times[0] - started)
            if completion_times
            else float("nan")
        ),
        "wait_time": _summary(wait_durations_ms),
        "completion_period": _summary(completion_periods_ms),
        "overall_rate_hz": (
            len(completion_times) / total_s
            if math.isfinite(total_s) and total_s > 0.0
            else float("nan")
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare iDus single-scan and continuous SDK2 acquisition timing."
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
    parser.add_argument(
        "--exposure-ms",
        type=float,
        action="append",
        help="Requested exposure; repeat for multiple values (default: SDK minimum).",
    )
    parser.add_argument(
        "--vertical-speed-index",
        type=int,
        action="append",
        help=(
            "Vertical-shift index to test; repeat to compare settings. "
            "The camera's 16 us/pixel-preferred default is used when omitted."
        ),
    )
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument(
        "--skip-continuous",
        action="store_true",
        help="Measure only the GUI-equivalent rearmed single-scan path.",
    )
    parser.add_argument("--output", default="andor_timing_probe.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if sys.platform != "win32":
        print("This diagnostic requires Windows.", file=sys.stderr)
        return 2
    frames = int(args.frames)
    if not 2 <= frames <= 1000:
        print("--frames must be between 2 and 1000.", file=sys.stderr)
        return 2

    camera = AndorSDK2Camera(
        Path(args.solis_dir),
        camera_index=int(args.camera_index),
        camera_dll=Path(args.camera_dll),
    )
    try:
        initial_settings = camera.settings
        initial_capabilities = camera.capabilities.as_control_schema()
        speed_indices = list(
            dict.fromkeys(
                int(value)
                for value in (
                    args.vertical_speed_index
                    if args.vertical_speed_index is not None
                    else [initial_settings.vertical_speed_index]
                )
            )
        )
        measurements: list[dict[str, Any]] = []
        for speed_index in speed_indices:
            try:
                camera.apply_settings(
                    replace(initial_settings, vertical_speed_index=speed_index)
                )
            except Exception as exc:
                measurements.append(
                    {
                        "mode": "configuration",
                        "vertical_speed_index": speed_index,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue

            minimum_ms = float(camera.capabilities.minimum_exposure_ms)
            exposures = [
                float(value) for value in (args.exposure_ms or [minimum_ms])
            ]
            vertical_speeds = camera.capabilities.vertical_speeds_us
            vertical_speed_us = (
                float(vertical_speeds[speed_index])
                if 0 <= speed_index < len(vertical_speeds)
                else float("nan")
            )
            configuration = {
                "camera_settings": asdict(camera.settings),
                "vertical_speed_us_per_pixel": vertical_speed_us,
                "minimum_exposure_ms": camera.capabilities.minimum_exposure_ms,
                "minimum_frame_period_ms": (
                    camera.capabilities.minimum_frame_period_ms
                ),
                "readout_time_ms": camera.capabilities.readout_time_ms,
                "keep_clean_time_ms": camera.capabilities.keep_clean_time_ms,
            }
            for exposure_ms in exposures:
                if not math.isfinite(exposure_ms) or exposure_ms < minimum_ms:
                    measurements.append(
                        {
                            "mode": "validation",
                            "requested_exposure_ms": exposure_ms,
                            **configuration,
                            "error": (
                                "Exposure is below the SDK-reported minimum "
                                f"of {minimum_ms:.6g} ms."
                            ),
                        }
                    )
                    continue
                try:
                    result = _single_scan_benchmark(
                        camera,
                        exposure_ms=exposure_ms,
                        frames=frames,
                    )
                    result.update(configuration)
                    measurements.append(result)
                except Exception as exc:
                    measurements.append(
                        {
                            "mode": "single_scan_rearmed_each_frame",
                            "requested_exposure_ms": exposure_ms,
                            **configuration,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                if not args.skip_continuous:
                    try:
                        result = _continuous_fvb_benchmark(
                            camera,
                            exposure_ms=exposure_ms,
                            frames=frames,
                        )
                        result.update(configuration)
                        measurements.append(result)
                    except Exception as exc:
                        measurements.append(
                            {
                                "mode": "run_till_abort_internal_fvb",
                                "requested_exposure_ms": exposure_ms,
                                **configuration,
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                        )
        report = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "python": sys.version,
            "platform": platform.platform(),
            "solis_dir": str(camera.solis_dir),
            "camera_dll": str(camera.dll_path),
            "initial_camera_settings": asdict(initial_settings),
            "initial_camera_capabilities": initial_capabilities,
            "measurements": measurements,
        }
    finally:
        camera.close()

    output = Path(args.output).expanduser().resolve()
    with atomic_text_writer(output) as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    print(f"Wrote Andor timing report to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
