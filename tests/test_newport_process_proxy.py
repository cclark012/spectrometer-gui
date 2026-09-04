from __future__ import annotations

import pytest

from devices.newport_process_proxy import (
    Newport2936RProcess,
    NewportProcessError,
    newport_connect_retry_delays,
)


class _LiveProcess:
    def is_alive(self) -> bool:
        return True


class _TimeoutConnection:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def send(self, value: object) -> None:
        self.sent.append(value)

    def poll(self, _timeout_s: float) -> bool:
        return False


def test_startup_is_quick_but_requested_reconnect_is_bounded() -> None:
    assert newport_connect_retry_delays(initial_attempt=True) == (0.0,)
    assert newport_connect_retry_delays(initial_attempt=False) == (
        0.0,
        0.75,
        1.5,
        3.0,
        5.0,
    )


def test_operation_timeout_retires_the_driver_process() -> None:
    proxy = Newport2936RProcess.__new__(Newport2936RProcess)
    proxy._closed = False
    proxy._process = _LiveProcess()
    proxy._connection = _TimeoutConnection()
    proxy._request_id = 0
    proxy.operation_timeout_s = 1.0
    close_calls: list[bool] = []
    proxy.close = lambda *, force=False: close_calls.append(bool(force))

    with pytest.raises(NewportProcessError, match="timed out"):
        proxy._call("identify")

    assert close_calls == [True]
