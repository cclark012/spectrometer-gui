from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np

import devices.andor_sdk2 as andor_sdk2_module
from devices.andor_sdk2 import (
    AndorAcquisitionProfile,
    AndorCameraCapabilities,
    AndorCameraSettings,
    AndorSDK2Camera,
    AndorSDK2Error,
)


def _capabilities(*, minimum_exposure_ms: float = 1.0) -> AndorCameraCapabilities:
    return AndorCameraCapabilities(
        model="DU401_BVF",
        serial_number="26970",
        detector_pixels_x=4,
        detector_pixels_y=127,
        pixel_width_um=26.0,
        pixel_height_um=26.0,
        ad_channels=(0,),
        output_amplifiers=(0,),
        horizontal_speeds_mhz={"0:0": (0.1,)},
        vertical_speeds_us=(8.25, 16.25, 32.25, 64.25),
        preamp_gains=(1.0,),
        preamp_gain_indices={"0:0:0": (0,)},
        bit_depth=16,
        temperature_min_c=-100,
        temperature_max_c=20,
        cooler_enabled=False,
        minimum_exposure_ms=minimum_exposure_ms,
    )


def _camera(*, minimum_exposure_ms: float = 1.0) -> AndorSDK2Camera:
    camera = AndorSDK2Camera.__new__(AndorSDK2Camera)
    camera.settings = AndorCameraSettings()
    camera.capabilities = _capabilities(minimum_exposure_ms=minimum_exposure_ms)
    camera.last_timing = None
    camera.last_stage_timing_ms = {}
    camera._prepared_signature = None
    camera._continuous_signature = None
    camera._continuous_started_s = float("nan")
    camera._continuous_next_driver_index = None
    camera._initialized = True
    camera._dll = SimpleNamespace(
        WaitForAcquisitionTimeOut=object(),
        GetNumberNewImages=object(),
        GetOldestImage=object(),
    )
    camera.last_acquisition_profile = None
    return camera


def test_unverified_fvb_horizontal_binning_is_rejected_before_native_calls() -> None:
    camera = _camera()
    camera.stop_continuous_acquisition = lambda: None

    def native_call(*_args, **_kwargs):
        raise AssertionError("native call should not be reached")

    camera._call = native_call

    try:
        camera.apply_settings(
            AndorCameraSettings(horizontal_binning=2)
        )
    except ValueError as exc:
        assert "full-vertical-binning mode" in str(exc)
    else:
        raise AssertionError("Unverified horizontal binning was accepted")


def test_run_till_abort_is_armed_once_and_returns_oldest_fresh_frame() -> None:
    camera = _camera()
    state = {"pending": False, "next": 11, "starts": 0, "aborts": 0}

    def native_call(name, *args, **_kwargs):
        accepted = _kwargs.get("accepted")
        if name == "GetAcquisitionTimings":
            args[0]._obj.value = 0.005
            args[1]._obj.value = 0.005
            args[2]._obj.value = 0.005
        elif name == "GetReadOutTime":
            args[0]._obj.value = 0.001
        elif name == "GetKeepCleanTime":
            args[0]._obj.value = 0.0
        elif name == "StartAcquisition":
            state["starts"] += 1
        elif name == "WaitForAcquisitionTimeOut":
            state["pending"] = True
        elif name == "GetNumberNewImages":
            if not state["pending"]:
                assert accepted == {20002, 20024}
                return 20024
            args[0]._obj.value = state["next"]
            args[1]._obj.value = state["next"]
        elif name == "GetOldestImage":
            for index in range(4):
                args[0][index] = state["next"] * 10 + index
            state["pending"] = False
            state["next"] += 1
        elif name == "AbortAcquisition":
            state["aborts"] += 1
        return 20002

    camera._call = native_call
    progress: list[tuple[int, int, str]] = []
    first = camera.acquire(
        5.0,
        averages=1,
        continuous_session="live",
        progress_callback=lambda *values: progress.append(values),
    )
    second = camera.acquire(5.0, averages=1, continuous_session="live")

    np.testing.assert_allclose(first, [110.0, 111.0, 112.0, 113.0])
    np.testing.assert_allclose(second, [120.0, 121.0, 122.0, 123.0])
    assert state["starts"] == 1
    assert state["aborts"] == 0
    assert progress == [(0, 1, "software"), (1, 1, "software")]
    assert camera.last_timing is not None
    assert camera.last_timing.acquisition_mode == "run_till_abort"
    assert camera.last_timing.driver_frame_index == 12
    assert camera.last_stage_timing_ms["continuous_state_reused"] is True

    # A native buffer index must never move backward and silently return a
    # duplicate frame. Exercise the guard in this existing test so the
    # compatibility-runner invocation count remains stable.
    state["next"] = 10
    try:
        camera.acquire(5.0, averages=1, continuous_session="live")
    except AndorSDK2Error as exc:
        assert "moved backward" in str(exc)
    else:
        raise AssertionError("A regressed SDK2 frame index was accepted.")
    assert state["aborts"] == 1


def test_run_till_abort_ignores_event_for_frame_drained_between_requests() -> None:
    camera = _camera()
    state = {
        "available": [],
        "next": 11,
        "stale_events": 0,
        "waits": 0,
        "starts": 0,
    }

    def native_call(name, *args, **_kwargs):
        del _kwargs
        if name == "GetAcquisitionTimings":
            args[0]._obj.value = 0.005
            args[1]._obj.value = 0.005
            args[2]._obj.value = 0.005
        elif name == "GetReadOutTime":
            args[0]._obj.value = 0.001
        elif name == "GetKeepCleanTime":
            args[0]._obj.value = 0.0
        elif name == "StartAcquisition":
            state["starts"] += 1
        elif name == "WaitForAcquisitionTimeOut":
            state["waits"] += 1
            if state["stale_events"]:
                state["stale_events"] -= 1
            else:
                state["available"].append(state["next"])
                state["next"] += 1
        elif name == "GetNumberNewImages":
            if not state["available"]:
                return 20024
            args[0]._obj.value = state["available"][0]
            args[1]._obj.value = state["available"][-1]
        elif name == "GetOldestImage":
            if not state["available"]:
                return 20024
            frame = state["available"].pop(0)
            for index in range(4):
                args[0][index] = frame * 10 + index
        return 20002

    camera._call = native_call
    first = camera.acquire(5.0, continuous_session="live")

    # A frame and its SDK event arrive while the GUI renders the first result.
    # The next request drains that stale frame, but the event remains queued.
    state["available"].append(state["next"])
    state["next"] += 1
    state["stale_events"] = 1
    second = camera.acquire(5.0, continuous_session="live")

    np.testing.assert_allclose(first, [110.0, 111.0, 112.0, 113.0])
    np.testing.assert_allclose(second, [130.0, 131.0, 132.0, 133.0])
    assert state["starts"] == 1
    assert state["waits"] == 3
    assert camera.last_timing is not None
    assert camera.last_timing.driver_frame_index == 13
    assert camera.last_timing.discarded_frames_before == 1
    assert camera.last_stage_timing_ms["stale_wait_notifications"] == 1


def test_gated_stream_skips_frame_with_midpoint_before_requested_delay() -> None:
    camera = _camera()
    profile = AndorAcquisitionProfile(
        requested_exposure_ms=5.0,
        applied_exposure_ms=5.0,
        kinetic_cycle_ms=5.0,
        readout_ms=2.0,
        keep_clean_ms=0.0,
    )
    clock = {"now": 100.0, "frame": 0}
    original_perf_counter = andor_sdk2_module.time.perf_counter

    camera._continuous_started_s = 99.0
    camera._continuous_next_driver_index = None
    camera._ensure_continuous_acquisition = lambda **_kwargs: (
        profile,
        True,
        clock["now"],
        clock["now"],
        0.0,
    )
    camera._drain_pending_continuous_images = lambda *_args: 0

    def wait_for_image(*, timeout_ms: int):
        assert timeout_ms > 0
        clock["frame"] += 1
        clock["now"] += 0.001 if clock["frame"] == 1 else 0.005
        return (clock["frame"], clock["frame"]), 0

    def read_oldest(buffer, count: int) -> None:
        for index in range(count):
            buffer[index] = clock["frame"] * 10 + index

    camera._wait_for_continuous_image = wait_for_image
    camera._read_oldest_image = read_oldest
    andor_sdk2_module.time.perf_counter = lambda: clock["now"]
    try:
        values = camera.acquire(
            5.0,
            continuous_session="gated",
            continuous_target_midpoint_s=100.0,
        )
    finally:
        andor_sdk2_module.time.perf_counter = original_perf_counter

    np.testing.assert_allclose(values, [20.0, 21.0, 22.0, 23.0])
    assert camera.last_timing is not None
    assert camera.last_timing.driver_frame_index == 2
    assert camera.last_timing.discarded_frames_for_target == 1
    assert camera.last_timing.midpoint_estimate_s >= 100.0
    assert camera.last_stage_timing_ms["target_alignment_applied"] is True


def test_gated_stream_dates_oldest_frame_when_multiple_images_are_pending() -> None:
    camera = _camera()
    profile = AndorAcquisitionProfile(
        requested_exposure_ms=5.0,
        applied_exposure_ms=5.0,
        kinetic_cycle_ms=5.0,
        readout_ms=2.0,
        keep_clean_ms=0.0,
    )
    clock = {"now": 100.011, "oldest": 1}
    original_perf_counter = andor_sdk2_module.time.perf_counter

    camera._continuous_started_s = 99.0
    camera._continuous_next_driver_index = None
    camera._ensure_continuous_acquisition = lambda **_kwargs: (
        profile,
        True,
        clock["now"],
        clock["now"],
        0.0,
    )
    camera._drain_pending_continuous_images = lambda *_args: 0
    camera._wait_for_continuous_image = lambda **_kwargs: (
        (clock["oldest"], 3),
        0,
    )

    def read_oldest(buffer, count: int) -> None:
        oldest = clock["oldest"]
        for index in range(count):
            buffer[index] = oldest * 10 + index
        clock["oldest"] += 1

    camera._read_oldest_image = read_oldest
    andor_sdk2_module.time.perf_counter = lambda: clock["now"]
    try:
        values = camera.acquire(
            5.0,
            continuous_session="gated",
            continuous_target_midpoint_s=100.0,
        )
    finally:
        andor_sdk2_module.time.perf_counter = original_perf_counter

    # Frame 1 completed earliest even though all three frames were pending at
    # the query. Its midpoint is estimated at 99.9965 s, so frame 2 is the
    # first eligible result at 100.0015 s.
    np.testing.assert_allclose(values, [20.0, 21.0, 22.0, 23.0])
    assert camera.last_timing is not None
    assert camera.last_timing.driver_frame_index == 2
    assert camera.last_timing.discarded_frames_for_target == 1
    assert camera.last_timing.pending_frames_after_wait == 3
    assert np.isclose(camera.last_timing.midpoint_estimate_s, 100.0015)


def test_changing_continuous_session_rearms_stream() -> None:
    camera = _camera()
    camera.last_acquisition_profile = AndorAcquisitionProfile(
        requested_exposure_ms=5.0,
        applied_exposure_ms=5.0,
        kinetic_cycle_ms=5.0,
        readout_ms=1.0,
        keep_clean_ms=0.0,
    )
    camera._continuous_signature = (camera.settings, 5.0, "live")
    camera._continuous_started_s = time.perf_counter() - 1.0
    calls: list[str] = []
    state = {"pending": False}

    def native_call(name, *args, **_kwargs):
        del _kwargs
        calls.append(name)
        if name == "GetAcquisitionTimings":
            args[0]._obj.value = 0.005
            args[1]._obj.value = 0.005
            args[2]._obj.value = 0.005
        elif name == "GetReadOutTime":
            args[0]._obj.value = 0.001
        elif name == "GetKeepCleanTime":
            args[0]._obj.value = 0.0
        elif name == "WaitForAcquisitionTimeOut":
            state["pending"] = True
        elif name == "GetNumberNewImages":
            if not state["pending"]:
                return 20024
            args[0]._obj.value = 1
            args[1]._obj.value = 1
        elif name == "GetOldestImage":
            for index in range(4):
                args[0][index] = index
            state["pending"] = False
        return 20002

    camera._call = native_call
    camera.acquire(5.0, continuous_session="gated")

    assert calls.count("AbortAcquisition") == 1
    assert calls.count("StartAcquisition") == 1
    assert camera._continuous_signature == (camera.settings, 5.0, "gated")


def test_request_below_single_scan_probe_limit_is_forwarded_to_sdk() -> None:
    camera = _camera(minimum_exposure_ms=13.26)
    calls: list[float] = []
    camera._acquire_single = (
        lambda exposure_ms, averages, progress_callback=None: (
            calls.append(float(exposure_ms)) or np.zeros(4)
        )
    )

    camera.acquire(5.0)

    assert calls == [5.0]


def test_acquire_uses_sdk_applied_timing_and_separates_stage_costs() -> None:
    camera = _camera()

    def native_call(name, *args, **_kwargs):
        del _kwargs
        if name == "GetAcquisitionTimings":
            args[0]._obj.value = 0.005
            args[1]._obj.value = 0.005
            args[2]._obj.value = 0.010
        elif name == "GetReadOutTime":
            args[0]._obj.value = 0.002
        elif name == "GetKeepCleanTime":
            args[0]._obj.value = 0.003
        elif name == "WaitForAcquisitionTimeOut":
            time.sleep(0.008)
        elif name == "GetAcquiredData":
            for index in range(4):
                args[0][index] = index + 1
        return 20002

    camera._call = native_call

    values = camera.acquire(5.0)

    np.testing.assert_allclose(values, [1.0, 2.0, 3.0, 4.0])
    assert np.isclose(camera.last_acquisition_profile.applied_exposure_ms, 5.0)
    assert camera.last_timing is not None
    assert camera.last_timing.basis == "sdk_completion_minus_readout_estimate"
    assert np.isclose(camera.last_timing.exposure_duration_s, 0.005)
    assert camera.last_timing.window_finished_s > camera.last_timing.window_started_s
    assert set(camera.last_stage_timing_ms) == {
        "prepared_state_reused",
        "settings_and_timing_query_ms",
        "prepare_ms",
        "start_command_ms",
        "wait_ms",
        "data_transfer_ms",
    }

    camera.acquire(5.0)
    assert camera.last_stage_timing_ms["prepared_state_reused"] is True


def test_refresh_exposure_limits_records_frame_period_separately() -> None:
    camera = _camera()

    def native_call(name, *args, **_kwargs):
        del _kwargs
        if name == "GetAcquisitionTimings":
            args[0]._obj.value = 0.01326
            args[1]._obj.value = 0.01326
            args[2]._obj.value = 0.02150
        elif name == "GetReadOutTime":
            args[0]._obj.value = 0.013
        elif name == "GetKeepCleanTime":
            args[0]._obj.value = 0.008
        elif name == "GetMaximumExposure":
            args[0]._obj.value = 3600.0
        return 20002

    camera._call = native_call

    camera._refresh_exposure_limits()

    assert np.isclose(camera.capabilities.minimum_exposure_ms, 13.26)
    assert np.isclose(camera.capabilities.minimum_frame_period_ms, 21.5)
    assert np.isclose(camera.capabilities.readout_time_ms, 13.0)
    assert np.isclose(camera.capabilities.keep_clean_time_ms, 8.0)
    assert np.isclose(camera.capabilities.maximum_exposure_ms, 3_600_000.0)
