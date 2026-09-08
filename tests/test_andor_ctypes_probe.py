from __future__ import annotations

from troubleshooting.andor_ctypes_probe import (
    CAMERA_RETURN_CODES,
    ResultRecorder,
    _camera_dll_path,
    parse_args,
)


def test_failed_native_call_does_not_read_output_buffer() -> None:
    output_read = False

    def read_output():
        nonlocal output_read
        output_read = True
        return 123

    recorder = ResultRecorder({1: "ERROR"}, success_codes={0})
    result = recorder.record("NativeCall", lambda: 1, value=read_output)

    assert not result.success
    assert result.value is None
    assert result.error == "ERROR"
    assert not output_read


def test_accepted_nonzero_status_can_return_a_value() -> None:
    recorder = ResultRecorder({5: "VALID_STATUS"}, success_codes={0})
    result = recorder.record(
        "Temperature",
        lambda: 5,
        value=lambda: -70,
        accepted_codes={0, 5},
    )

    assert result.success
    assert result.value == -70


def test_sdk2_return_codes_match_vendor_table() -> None:
    assert CAMERA_RETURN_CODES[20013] == "DRV_ERROR_ACK"
    assert CAMERA_RETURN_CODES[20073] == "DRV_IDLE"
    assert CAMERA_RETURN_CODES[20075] == "DRV_NOT_INITIALIZED"
    assert CAMERA_RETURN_CODES[20121] == "DRV_ERROR_NOHANDLE"
    assert CAMERA_RETURN_CODES[20990] == "DRV_ERROR_NOCAMERA"
    assert CAMERA_RETURN_CODES[20992] == "DRV_NOT_AVAILABLE"


def test_legacy_camera_dll_is_preferred(tmp_path) -> None:
    modern = tmp_path / "atmcd64d.dll"
    legacy = tmp_path / "atmcd64d_legacy.dll"
    modern.touch()
    legacy.touch()

    assert _camera_dll_path(tmp_path) == legacy.resolve()
    assert _camera_dll_path(tmp_path, modern.name) == modern.resolve()


def test_camera_only_probe_scope_is_selectable() -> None:
    args = parse_args(["--camera-only", "--camera-dll", "atmcd64d_legacy.dll"])

    assert args.camera_only
    assert not args.spectrograph_only
    assert args.camera_dll == "atmcd64d_legacy.dll"
