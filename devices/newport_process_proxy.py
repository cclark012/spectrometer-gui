from __future__ import annotations

"""Restartable process boundary for Newport's process-global .NET wrapper.

The vendor assembly can retain stale USB discovery state after a 2936-R power
cycle even after its managed object is disposed and garbage-collected.  Keeping
the CLR and vendor object in a dedicated child process makes disconnect/reconnect
equivalent to a genuinely fresh Python process.
"""

import multiprocessing
import traceback
from pathlib import Path
from typing import Any

from core.records import PowerSnapshot


class NewportProcessError(RuntimeError):
    pass


def newport_connect_retry_delays(*, initial_attempt: bool) -> tuple[float, ...]:
    """Return the bounded discovery schedule for startup or an explicit reconnect."""

    if initial_attempt:
        return (0.0,)
    return (0.0, 0.75, 1.5, 3.0, 5.0)


_ALLOWED_METHODS = frozenset(
    {
        "identify",
        "diagnostics",
        "read_all_power_with_status",
        "set_wavelength_for_laser_nm",
        "get_wavelength_nm",
    }
)


def _newport_worker(connection, configuration: dict[str, Any]) -> None:
    meter = None
    try:
        # Import pythonnet only in this child.  Killing/restarting the process
        # consequently resets the CLR and every static object in the Newport DLL.
        from devices.newport_2936r_dotnet import Newport2936R

        meter = Newport2936R(
            configuration["dll_path"],
            channel=int(configuration["channel"]),
            units=int(configuration["units"]),
            logging=bool(configuration["logging"]),
        )
        connection.send({"kind": "ready"})
    except BaseException:
        try:
            connection.send(
                {
                    "kind": "startup_error",
                    "error": traceback.format_exc(),
                }
            )
        finally:
            connection.close()
        return

    try:
        while True:
            try:
                request = connection.recv()
            except EOFError:
                break
            if not isinstance(request, dict):
                continue
            if request.get("kind") == "close":
                break
            request_id = request.get("id")
            method_name = str(request.get("method", ""))
            if method_name not in _ALLOWED_METHODS:
                connection.send(
                    {
                        "kind": "error",
                        "id": request_id,
                        "error": f"Unsupported Newport proxy method: {method_name!r}",
                    }
                )
                continue
            try:
                method = getattr(meter, method_name)
                result = method(*tuple(request.get("args", ())))
                connection.send({"kind": "result", "id": request_id, "value": result})
            except BaseException:
                connection.send(
                    {
                        "kind": "error",
                        "id": request_id,
                        "error": traceback.format_exc(),
                    }
                )
    finally:
        if meter is not None:
            try:
                meter.close()
            except Exception:
                pass
        connection.close()


class Newport2936RProcess:
    """PowerMeterAdapter-compatible RPC proxy with deterministic teardown."""

    def __init__(
        self,
        dll_path: str | Path,
        *,
        channel: int = 1,
        units: int = 2,
        logging: bool = False,
        startup_timeout_s: float = 30.0,
        operation_timeout_s: float = 30.0,
    ) -> None:
        self.dll_path = Path(dll_path)
        self.channel = int(channel)
        self.units = int(units)
        self.logging = bool(logging)
        self.operation_timeout_s = max(1.0, float(operation_timeout_s))
        self._request_id = 0
        self._closed = False

        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        self._connection = parent_connection
        self._process = context.Process(
            target=_newport_worker,
            args=(
                child_connection,
                {
                    "dll_path": str(self.dll_path),
                    "channel": self.channel,
                    "units": self.units,
                    "logging": self.logging,
                },
            ),
            name="Newport2936R",
            daemon=True,
        )
        self._process.start()
        child_connection.close()

        if not self._connection.poll(max(1.0, float(startup_timeout_s))):
            self.close(force=True)
            raise NewportProcessError(
                "Timed out while starting the isolated Newport driver process."
            )
        try:
            response = self._connection.recv()
        except (EOFError, OSError) as exc:
            self.close(force=True)
            raise NewportProcessError(
                "The isolated Newport driver process exited during startup."
            ) from exc
        if response.get("kind") != "ready":
            error = str(response.get("error", "Unknown Newport startup error."))
            self.close(force=True)
            raise NewportProcessError(error)

    def _call(self, method: str, *args: object) -> object:
        if self._closed or not self._process.is_alive():
            raise NewportProcessError("The isolated Newport driver process is not running.")
        self._request_id += 1
        request_id = self._request_id
        try:
            self._connection.send(
                {
                    "kind": "call",
                    "id": request_id,
                    "method": str(method),
                    "args": tuple(args),
                }
            )
        except (BrokenPipeError, EOFError, OSError) as exc:
            raise NewportProcessError(
                "Communication with the isolated Newport driver process failed."
            ) from exc

        if not self._connection.poll(self.operation_timeout_s):
            # A timed-out native call cannot be cancelled safely in-process.
            # Retire the entire CLR process so the next Connect starts from a
            # clean USB-discovery state and cannot receive a stale RPC result.
            self.close(force=True)
            raise NewportProcessError(
                f"Newport operation {method} timed out after "
                f"{self.operation_timeout_s:g} s."
            )
        try:
            response = self._connection.recv()
        except (EOFError, OSError) as exc:
            raise NewportProcessError(
                "The isolated Newport driver process exited unexpectedly."
            ) from exc
        if response.get("id") != request_id:
            self.close(force=True)
            raise NewportProcessError("Received an out-of-order Newport response.")
        if response.get("kind") == "error":
            raise NewportProcessError(str(response.get("error", "Newport operation failed.")))
        return response.get("value")

    def identify(self) -> str:
        return str(self._call("identify"))

    def diagnostics(self) -> dict[str, object]:
        result = self._call("diagnostics")
        if not isinstance(result, dict):
            raise NewportProcessError("Newport returned invalid driver diagnostics.")
        return dict(result)

    def read_all_power_with_status(self) -> PowerSnapshot:
        result = self._call("read_all_power_with_status")
        if not isinstance(result, PowerSnapshot):
            raise NewportProcessError("Newport returned an invalid power snapshot.")
        return result

    def set_wavelength_for_laser_nm(self, wavelength_nm: float) -> int:
        return int(self._call("set_wavelength_for_laser_nm", float(wavelength_nm)))

    def get_wavelength_nm(self) -> int:
        return int(self._call("get_wavelength_nm"))

    def close(self, *, force: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        process = getattr(self, "_process", None)
        connection = getattr(self, "_connection", None)
        if connection is not None and process is not None and process.is_alive() and not force:
            try:
                connection.send({"kind": "close"})
            except Exception:
                pass
            process.join(timeout=5.0)
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def __enter__(self) -> Newport2936RProcess:
        return self

    def __exit__(self, exc_type, exc, traceback_object) -> None:
        del exc_type, exc, traceback_object
        self.close()

    def __del__(self) -> None:
        try:
            self.close(force=True)
        except Exception:
            pass
