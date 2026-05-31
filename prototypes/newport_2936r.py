from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol
import time


UTC = timezone.utc


class Transport(Protocol):
    def query(self, command: str) -> str: ...
    def write(self, command: str) -> None: ...
    def close(self) -> None: ...


@dataclass(slots=True)
class PowerMeasurement:
    timestamp_utc: str
    channel: int
    value: float
    units_code: Optional[int] = None
    units_name: Optional[str] = None


@dataclass(slots=True)
class PowerStatusMeasurement:
    timestamp_utc: str
    power_1: float
    status_1_raw: str
    power_2: float
    status_2_raw: str


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _units_name(units_code: int) -> str:
    mapping = {
        0: "A",
        1: "V",
        2: "W",
        3: "W/cm^2",
        4: "J",
        5: "J/cm^2",
        6: "dBm",
        11: "sun",
    }
    return mapping.get(units_code, f"code_{units_code}")


class SerialTransport:
    """
    RS-232 transport for Newport 1936-R / 2936-R class meters.

    Uses the settings documented by Newport for RS-232:
    38400 baud, 8 data bits, no parity, 1 stop bit.
    Commands are terminated with CR; responses end with CRLF.
    """

    def __init__(self, port: str, timeout_s: float = 1.0, write_termination: str = "\r") -> None:
        try:
            import serial  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pyserial is not installed. Run: pip install pyserial") from exc

        self._serial_mod = serial
        self._port = port
        self._timeout_s = timeout_s
        self._write_termination = write_termination
        self._ser = serial.Serial(
            port=port,
            baudrate=38400,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout_s,
            write_timeout=timeout_s,
        )
        self._drain_buffers()
        # Default is echo-off, but force it once to avoid query parsing issues.
        self.write("ECHO 0")
        self._drain_buffers()

    @property
    def port(self) -> str:
        return self._port

    def _drain_buffers(self) -> None:
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

    def _write_bytes(self, payload: str) -> None:
        self._ser.write(payload.encode("ascii"))
        self._ser.flush()

    def write(self, command: str) -> None:
        self._write_bytes(f"{command}{self._write_termination}")
        # Give the instrument a moment to process and emit any echo/prompt.
        time.sleep(0.03)
        # Drain any non-query response noise.
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            data = self._ser.readline()
            if not data:
                break

    def query(self, command: str) -> str:
        self._ser.reset_input_buffer()
        self._write_bytes(f"{command}{self._write_termination}")

        candidates: list[str] = []
        deadline = time.monotonic() + self._timeout_s
        while time.monotonic() < deadline:
            raw = self._ser.readline()
            if not raw:
                continue
            text = raw.decode("ascii", errors="replace").strip()
            text = text.lstrip(">").strip()
            if not text:
                continue
            if text == command:
                continue
            candidates.append(text)
            quiet_deadline = time.monotonic() + 0.03
            while time.monotonic() < quiet_deadline:
                raw2 = self._ser.readline()
                if not raw2:
                    continue
                text2 = raw2.decode("ascii", errors="replace").strip()
                text2 = text2.lstrip(">").strip()
                if text2 and text2 != command:
                    candidates.append(text2)
                    quiet_deadline = time.monotonic() + 0.03
            break

        if not candidates:
            raise TimeoutError(f"Timed out waiting for response to {command!r} on {self._port}")
        return candidates[-1]

    def close(self) -> None:
        try:
            if self._ser.is_open:
                self._ser.close()
        except Exception:
            pass


class VisaTransport:
    """
    VISA transport.

    This is useful if your serial adapter or meter is exposed as a VISA resource.
    Typical RS-232 resource strings look like ASRL5::INSTR.

    For RS-232 use write_termination='\r' and read_termination='\n'.
    For direct USB use you may need write_termination='' because Newport documents
    that USB does not require a termination character.
    """

    def __init__(
        self,
        resource_name: str,
        timeout_s: float = 1.0,
        write_termination: str = "\r",
        read_termination: str = "\n",
    ) -> None:
        try:
            import pyvisa  # type: ignore
        except ImportError as exc:
            raise RuntimeError("PyVISA is not installed. Run: pip install pyvisa") from exc

        self._pyvisa = pyvisa
        self._rm = pyvisa.ResourceManager()
        self._inst = self._rm.open_resource(resource_name)
        self._inst.timeout = int(timeout_s * 1000)
        self._inst.write_termination = write_termination
        self._inst.read_termination = read_termination

        # Configure serial attributes only when they exist.
        if hasattr(self._inst, "baud_rate"):
            self._inst.baud_rate = 38400
        if hasattr(self._inst, "data_bits"):
            self._inst.data_bits = 8
        if hasattr(self._inst, "stop_bits"):
            self._inst.stop_bits = pyvisa.constants.StopBits.one
        if hasattr(self._inst, "parity"):
            self._inst.parity = pyvisa.constants.Parity.none

        try:
            self.write("ECHO 0")
        except Exception:
            pass

    def write(self, command: str) -> None:
        self._inst.write(command)

    def query(self, command: str) -> str:
        return str(self._inst.query(command)).strip()

    def close(self) -> None:
        try:
            self._inst.close()
        finally:
            try:
                self._rm.close()
            except Exception:
                pass


class Newport2936R:
    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def close(self) -> None:
        self._t.close()

    def __enter__(self) -> "Newport2936R":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def identify(self) -> str:
        return self._t.query("*IDN?")

    def set_channel(self, channel: int) -> None:
        if channel not in (1, 2):
            raise ValueError("channel must be 1 or 2")
        self._t.write(f"PM:CHAN {channel}")

    def get_channel(self) -> int:
        return int(self._t.query("PM:CHAN?"))

    def set_units(self, units_code: int) -> None:
        self._t.write(f"PM:UNITS {units_code}")

    def get_units_code(self) -> int:
        return int(self._t.query("PM:UNITS?"))

    def get_units_name(self) -> str:
        return _units_name(self.get_units_code())

    def set_run(self, enabled: bool = True) -> None:
        self._t.write(f"PM:RUN {1 if enabled else 0}")

    def is_running(self) -> bool:
        return bool(int(self._t.query("PM:RUN?")))

    def set_filter_mode(self, mode: int) -> None:
        if mode not in (0, 1, 2, 3):
            raise ValueError("filter mode must be 0, 1, 2, or 3")
        self._t.write(f"PM:FILT {mode}")

    def read_power(self, channel: int = 1) -> PowerMeasurement:
        self.set_channel(channel)
        units_code = self.get_units_code()
        value = float(self._t.query("PM:P?"))
        return PowerMeasurement(
            timestamp_utc=_utc_now_iso(),
            channel=channel,
            value=value,
            units_code=units_code,
            units_name=_units_name(units_code),
        )

    def read_power_with_status(self) -> PowerStatusMeasurement:
        raw = self._t.query("PM:PWS?")
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError(f"Unexpected PM:PWS? response: {raw!r}")
        return PowerStatusMeasurement(
            timestamp_utc=_utc_now_iso(),
            power_1=float(parts[0]),
            status_1_raw=parts[1],
            power_2=float(parts[2]),
            status_2_raw=parts[3],
        )


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError:
        return []
    return [port.device for port in list_ports.comports()]


def list_visa_resources() -> list[str]:
    try:
        import pyvisa  # type: ignore
    except ImportError:
        return []
    rm = pyvisa.ResourceManager()
    try:
        return list(rm.list_resources())
    finally:
        rm.close()
