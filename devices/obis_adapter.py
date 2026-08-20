from __future__ import annotations

import math
import re
import time

import serial
from serial.tools import list_ports

from core.laser_models import LaserChannelInfo, LaserEmissionState

_FLOAT_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")

BOX_CHANNEL = 0
FIRST_LASER_CHANNEL = 1
DEFAULT_LASER_CHANNEL_COUNT = 5


def laser_channel_numbers(
    max_channels: int = DEFAULT_LASER_CHANNEL_COUNT) -> range:
    return range(FIRST_LASER_CHANNEL, FIRST_LASER_CHANNEL + int(max_channels))


def _laser_channel(channel: int) -> int:
    value = int(channel)
    if value < FIRST_LASER_CHANNEL:
        raise ValueError(
            f"OBIS laser channels start at {FIRST_LASER_CHANNEL}; got {value}."
        )
    return value


class ObisError(RuntimeError):
    pass


class ObisDisconnectedError(ObisError):
    pass


def _first_float(text: str, default: float = float("nan")) -> float:
    match = _FLOAT_RE.search(str(text))
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _state_from_text(text: str) -> LaserEmissionState:
    value = str(text).strip().lower()
    if value in {"on", "1", "true"}:
        return LaserEmissionState.ON
    if value in {"off", "0", "false"}:
        return LaserEmissionState.OFF
    return LaserEmissionState.UNKNOWN


class ObisBox:
    """Serial adapter for one Coherent OBIS Laser Box."""

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 115200,
        timeout_s: float = 0.5,
    ) -> None:
        self.port = str(port)
        self.baudrate = int(baudrate)
        self.timeout_s = float(timeout_s)
        self._known_channels: set[int] = set()

        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout_s,
            write_timeout=self.timeout_s,
        )

        self.box_id = self.identify_box()
        if not self.box_id:
            self.close()
            raise ObisError(f"{self.port} did not respond as an OBIS laser box.")

    def close(self) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()

    def query_lines(self, command: str, *, timeout_s: float | None = None) -> list[str]:
        timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        old_timeout = self.ser.timeout
        self.ser.timeout = timeout

        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self.ser.write(command.encode("ascii") + b"\r")
            self.ser.flush()

            lines: list[str] = []
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                raw = self.ser.readline()
                if not raw:
                    break

                text = raw.decode("ascii", errors="replace").strip()
                if not text:
                    continue

                lines.append(text)
                if text.upper() == "OK":
                    break

            return lines
        finally:
            self.ser.timeout = old_timeout

    def query_value(
        self,
        command: str,
        *,
        default: str = "",
        timeout_s: float | None = None,
    ) -> str:
        for line in self.query_lines(command, timeout_s=timeout_s):
            value = line.strip()
            if not value or value.upper() == "OK":
                continue
            if value.upper().startswith("ERR"):
                continue
            return value
        return default

    def optional_query_value(
        self,
        command: str,
        *,
        default: str = "",
        timeout_s: float | None = None,
    ) -> str:
        try:
            return self.query_value(command, default=default, timeout_s=timeout_s)
        except Exception:
            return default

    def write_command(self, command: str) -> None:
        lines = self.query_lines(command)
        for line in lines:
            if line.upper().startswith("ERR"):
                raise ObisError(f"{self.port}: {command!r} returned {line!r}")

    def _try_first_successful_write(self, commands: list[str]) -> str:
        errors: list[str] = []
        for command in commands:
            try:
                self.write_command(command)
                return command
            except Exception as exc:
                errors.append(f"{command!r}: {exc}")
        raise ObisError("All command variants failed:\n" + "\n".join(errors))

    def _try_first_successful_query(self, commands: list[str]) -> str:
        errors: list[str] = []
        for command in commands:
            try:
                response = self.query_value(command, default="", timeout_s=0.5)
                if response:
                    return response
            except Exception as exc:
                errors.append(f"{command!r}: {exc}")
        raise ObisError("All query variants failed:\n" + "\n".join(errors))

    def identify_box(self) -> str:
        identification = self.optional_query_value("*IDN?", timeout_s=0.5)
        if identification:
            return identification

        # Channel 0 identifies the Laser Box controller/hub.
        identification = self.optional_query_value(
            f"*IDN{BOX_CHANNEL}?",
            timeout_s=0.5,
        )

        if identification:
            return f"{self.port} {identification}"

        return ""

    def channel_present(self, channel: int) -> bool:
        channel = int(channel)
        probes = (
            f"*IDN{channel}?",
            f"SYSTem{channel}:INFormation:WAVelength?",
            f"SYSTem{channel}:INFormation:POWer?",
        )
        for command in probes:
            if self.optional_query_value(command, timeout_s=0.2):
                return True
        return False

    def discover_channels(
            self,
            max_channels: int = DEFAULT_LASER_CHANNEL_COUNT
        ) -> list[LaserChannelInfo]:

        channels: list[LaserChannelInfo] = []
        for channel in laser_channel_numbers(max_channels):
            if not self.channel_present(channel):
                continue
            try:
                channels.append(self.read_channel_info(channel))
            except Exception:
                continue

        self._known_channels = {item.channel for item in channels}
        return channels

    def read_channel_info(self, channel: int) -> LaserChannelInfo:
        channel = _laser_channel(channel)
        query = lambda command: self.optional_query_value(command, timeout_s=0.3) # noqa

        try:
            cdrh_enabled: bool | None = self.get_cdrh_delay(channel)
        except Exception:
            cdrh_enabled = None

        info = LaserChannelInfo(
            port=self.port,
            box_id=self.box_id,
            channel=channel,
            idn=query(f"*IDN{channel}?"),
            wavelength_nm=_first_float(
                query(f"SYSTem{channel}:INFormation:WAVelength?")
            ),
            nominal_power_w=_first_float(
                query(f"SYSTem{channel}:INFormation:POWer?")
            ),
            min_setpoint_w=_first_float(
                query(f"SOURce{channel}:POWer:LIMit:LOW?")
            ),
            max_setpoint_w=_first_float(
                query(f"SOURce{channel}:POWer:LIMit:HIGH?")
            ),
            setpoint_w=_first_float(
                query(f"SOURce{channel}:POWer:LEVel:IMMediate:AMPLitude?")
            ),
            output_power_w=_first_float(query(f"SOURce{channel}:POWer:LEVel?")),
            enabled=_state_from_text(query(f"SOURce{channel}:AM:STATe?")),
            cdrh_delay_enabled=cdrh_enabled,
        )
        self._known_channels.add(channel)
        return info

    def set_power_w(self, channel: int, power_w: float) -> None:
        channel = _laser_channel(channel)
        power_w = float(power_w)
        if not math.isfinite(power_w) or power_w < 0:
            raise ValueError(f"Invalid OBIS power setpoint: {power_w!r}")
        self.write_command(
            f"SOURce{channel}:POWer:LEVel:IMMediate:AMPLitude {power_w:.9e}"
        )

    def get_power_setpoint_w(self, channel: int) -> float:
        channel = _laser_channel(channel)
        response = self.query_value(
            f"SOURce{channel}:POWer:LEVel:IMMediate:AMPLitude?"
        )
        return _first_float(response)

    def set_enabled(self, channel: int, enabled: bool) -> None:
        channel = _laser_channel(channel)
        state = "ON" if enabled else "OFF"
        self.write_command(f"SOURce{channel}:AM:STATe {state}")

    def get_enabled(self, channel: int) -> LaserEmissionState:
        channel = _laser_channel(channel)
        response = self.query_value(f"SOURce{channel}:AM:STATe?")
        return _state_from_text(response)

    def set_cdrh_delay(self, channel: int, enabled: bool) -> str:
        channel = _laser_channel(channel)
        state = "ON" if enabled else "OFF"
        return self._try_first_successful_write(
            [
                f"SYSTem{channel}:CDRH {state}",
                f"SYSTem:CDRH {state}",
            ]
        )

    def get_cdrh_delay(self, channel: int) -> bool:
        channel = _laser_channel(channel)
        response = self._try_first_successful_query(
            [f"SYSTem{channel}:CDRH?", "SYSTem:CDRH?"]
        )
        return response.strip().upper() in {"ON", "1", "TRUE"}

    def disable_all(self, max_channels: int = DEFAULT_LASER_CHANNEL_COUNT) -> None:
        channels = sorted(
            channel for channel in self._known_channels if channel >= FIRST_LASER_CHANNEL
        )
        if not channels:
            channels = [
                channel
                for channel in laser_channel_numbers(max_channels)
                if self.channel_present(channel)
            ]

        errors: list[str] = []
        for channel in channels:
            try:
                self.set_enabled(channel, False)
            except Exception as exc:
                errors.append(f"ch{channel}: {exc}")

        if errors:
            raise ObisError("; ".join(errors))


def list_serial_port_names() -> list[str]:
    return [port.device for port in list_ports.comports()]


def find_obis_boxes(
    *,
    candidate_ports: list[str] | None = None,
    baudrate: int = 115200,
    timeout_s: float = 0.5,
) -> list[ObisBox]:
    ports = candidate_ports if candidate_ports is not None else list_serial_port_names()
    boxes: list[ObisBox] = []

    for port in ports:
        try:
            box = ObisBox(port, baudrate=baudrate, timeout_s=timeout_s)
            if box.discover_channels():
                boxes.append(box)
            else:
                box.close()
        except Exception:
            continue

    return boxes
