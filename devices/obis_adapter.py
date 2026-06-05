# devices/obis_adapter.py

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports

import numpy as np

from core.laser_models import LaserChannelInfo, LaserEmissionState


_FLOAT_RE = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
)


class ObisError(RuntimeError):
    pass


def _first_float(text: str, default: float = float("nan")) -> float:
    match = _FLOAT_RE.search(str(text))

    if not match:
        return default

    try:
        return float(match.group(0))
    except Exception:
        return default


def _state_from_text(text: str) -> LaserEmissionState:
    s = str(text).strip().lower()

    if s in {"on", "1", "true"}:
        return LaserEmissionState.ON

    if s in {"off", "0", "false"}:
        return LaserEmissionState.OFF

    return LaserEmissionState.UNKNOWN


class ObisBox:
    """
    Serial/USB-virtual-COM adapter for a Coherent OBIS Laser Box.

    The GUI should treat all powers internally as watts.
    """

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 115200,
        timeout_s: float = 0.50,
    ):
        self.port = str(port)
        self.baudrate = int(baudrate)
        self.timeout_s = float(timeout_s)

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


    def _try_first_successful_write(self, commands: list[str]) -> str:
        errors = []

        for command in commands:
            try:
                self.write_command(command)
                return command
            except Exception as exc:
                errors.append(f"{command!r}: {exc}")

        raise ObisError("All command variants failed:\n" + "\n".join(errors))


    def _try_first_successful_query(self, commands: list[str]) -> tuple[str, str]:
        errors = []

        for command in commands:
            try:
                response = self.query_value(command, default="", timeout_s=0.5)

                if response:
                    return command, response

            except Exception as exc:
                errors.append(f"{command!r}: {exc}")

        raise ObisError("All query variants failed:\n" + "\n".join(errors))


    def set_cdrh_delay(self, channel: int, enabled: bool) -> str:
        ch = int(channel)
        state = "ON" if enabled else "OFF"

        # Channel-indexed form first for Laser Box-style addressing.
        # Unindexed fallback for single-head OBIS-style addressing.
        return self._try_first_successful_write(
            [
                f"SYSTem{ch}:CDRH {state}",
                f"SYSTem:CDRH {state}",
            ]
        )


    def get_cdrh_delay(self, channel: int) -> bool:
        ch = int(channel)

        command, response = self._try_first_successful_query(
            [
                f"SYSTem{ch}:CDRH?",
                "SYSTem:CDRH?",
            ]
        )

        return response.strip().upper() in {"ON", "1", "TRUE"}


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

            payload = command.encode("ascii") + b"\r"
            self.ser.write(payload)
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
        lines = self.query_lines(command, timeout_s=timeout_s)

        useful = []

        for line in lines:
            s = line.strip()

            if not s:
                continue

            if s.upper() == "OK":
                continue

            if s.upper().startswith("ERR"):
                continue

            useful.append(s)

        if not useful:
            return default

        return useful[0]


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

    def identify_box(self) -> str:
        # Fast box-level ID first.
        idn = self.optional_query_value("*IDN?", default="", timeout_s=0.50)

        if idn:
            return idn

        # Some boxes respond better to channel ID queries.
        # Keep this short; this is only discovery.
        for ch in range(0, 6):
            idn_ch = self.optional_query_value(f"*IDN{ch}?", default="", timeout_s=0.25)

            if idn_ch:
                return f"{self.port} {idn_ch}"

        return ""

    def channel_present(self, channel: int) -> bool:
        ch = int(channel)

        idn = self.optional_query_value(f"*IDN{ch}?", default="", timeout_s=0.20)

        if idn:
            return True

        wavelength = self.optional_query_value(
            f"SYSTem{ch}:INFormation:WAVelength?",
            default="",
            timeout_s=0.20,
        )

        if wavelength:
            return True

        nominal_power = self.optional_query_value(
            f"SYSTem{ch}:INFormation:POWer?",
            default="",
            timeout_s=0.20,
        )

        return bool(nominal_power)

    def discover_channels(self, max_channels: int = 5) -> list[LaserChannelInfo]:
        channels: list[LaserChannelInfo] = []

        for ch in range(max_channels):
            if not self.channel_present(ch):
                continue

            try:
                info = self.read_channel_info(ch)
            except Exception:
                continue

            channels.append(info)

        return channels

    def read_channel_info(self, channel: int) -> LaserChannelInfo:
        ch = int(channel)

        idn = self.optional_query_value(f"*IDN{ch}?", default="", timeout_s=0.30)

        wavelength_text = self.optional_query_value(
            f"SYSTem{ch}:INFormation:WAVelength?",
            default="",
            timeout_s=0.30,
        )
        nominal_power_text = self.optional_query_value(
            f"SYSTem{ch}:INFormation:POWer?",
            default="",
            timeout_s=0.30,
        )

        min_text = self.optional_query_value(
            f"SOURce{ch}:POWer:LIMit:LOW?",
            default="",
            timeout_s=0.30,
        )
        max_text = self.optional_query_value(
            f"SOURce{ch}:POWer:LIMit:HIGH?",
            default="",
            timeout_s=0.30,
        )
        setpoint_text = self.optional_query_value(
            f"SOURce{ch}:POWer:LEVel:IMMediate:AMPLitude?",
            default="",
            timeout_s=0.30,
        )

        output_power_text = self.optional_query_value(
            f"SOURce{ch}:POWer:LEVel?",
            default="",
            timeout_s=0.30,
        )

        enabled_text = self.optional_query_value(
            f"SOURce{ch}:AM:STATe?",
            default="",
            timeout_s=0.30,
        )

        try:
            cdrh_delay_enabled = self.get_cdrh_delay(ch)
        except Exception:
            cdrh_delay_enabled = None

        return LaserChannelInfo(
            port=self.port,
            box_id=self.box_id,
            channel=ch,
            idn=idn,
            wavelength_nm=_first_float(wavelength_text),
            nominal_power_w=_first_float(nominal_power_text),
            min_setpoint_w=_first_float(min_text),
            max_setpoint_w=_first_float(max_text),
            setpoint_w=_first_float(setpoint_text),
            output_power_w=_first_float(output_power_text),
            enabled=_state_from_text(enabled_text),
            cdrh_delay_enabled=cdrh_delay_enabled,
        )

    def set_power_w(self, channel: int, power_w: float) -> None:
        ch = int(channel)
        p = float(power_w)

        if not math.isfinite(p) or p < 0:
            raise ValueError(f"Invalid OBIS power setpoint: {power_w!r}")

        self.write_command(
            f"SOURce{ch}:POWer:LEVel:IMMediate:AMPLitude {p:.9e}"
        )

    def get_power_setpoint_w(self, channel: int) -> float:
        text = self.query_value(
            f"SOURce{int(channel)}:POWer:LEVel:IMMediate:AMPLitude?"
        )
        return _first_float(text)

    def set_enabled(self, channel: int, enabled: bool) -> None:
        state = "ON" if enabled else "OFF"
        self.write_command(f"SOURce{int(channel)}:AM:STATe {state}")

    def get_enabled(self, channel: int) -> LaserEmissionState:
        text = self.query_value(f"SOURce{int(channel)}:AM:STATe?")
        return _state_from_text(text)

    def disable_all(self, max_channels: int = 5) -> None:
        errors = []

        for ch in range(max_channels):
            try:
                self.set_enabled(ch, False)
            except Exception as exc:
                errors.append(f"ch{ch}: {exc}")

        if errors:
            raise ObisError("; ".join(errors))


def list_serial_port_names() -> list[str]:
    return [p.device for p in list_ports.comports()]


def find_obis_boxes(
    *,
    candidate_ports: list[str] | None = None,
    baudrate: int = 115200,
    timeout_s: float = 0.50,
) -> list[ObisBox]:
    ports = candidate_ports if candidate_ports is not None else list_serial_port_names()

    boxes: list[ObisBox] = []

    for port in ports:
        try:
            box = ObisBox(
                port,
                baudrate=baudrate,
                timeout_s=timeout_s,
            )

            channels = box.discover_channels()

            if channels:
                boxes.append(box)
            else:
                box.close()

        except Exception:
            continue

    return boxes


@dataclass
class _EmulatedLaserState:
    channel: int
    wavelength_nm: float
    nominal_power_w: float
    min_setpoint_w: float
    max_setpoint_w: float
    setpoint_w: float
    enabled: LaserEmissionState = LaserEmissionState.OFF
    idn: str = ""
    cdrh_delay_enabled: bool = True


class EmulatedObisBox:
    """
    Drop-in emulator for ObisBox.

    Provides the same methods used by LaserController:
        discover_channels()
        read_channel_info(channel)
        set_power_w(channel, power_w)
        get_power_setpoint_w(channel)
        set_enabled(channel, enabled)
        get_enabled(channel)
        disable_all()
        close()
    """

    def __init__(
        self,
        port: str,
        *,
        serial: str,
        channels: list[_EmulatedLaserState],
        noise_fraction: float = 0.002,
    ):
        self.port = str(port)
        self.serial = str(serial)
        self.box_id = f"EMULATED OBIS Laser Box {self.serial}"
        self.noise_fraction = float(noise_fraction)
        self.rng = np.random.default_rng(abs(hash((self.port, self.serial))) % (2**32))

        self._channels: dict[int, _EmulatedLaserState] = {
            int(ch.channel): ch for ch in channels
        }

    def close(self) -> None:
        pass

    def discover_channels(self, max_channels: int = 5) -> list[LaserChannelInfo]:
        return [
            self.read_channel_info(ch)
            for ch in sorted(self._channels)
        ]

    def read_channel_info(self, channel: int) -> LaserChannelInfo:
        ch = int(channel)

        if ch not in self._channels:
            raise KeyError(f"Emulated OBIS {self.port} has no channel {ch}")

        state = self._channels[ch]

        if state.enabled == LaserEmissionState.ON:
            noise = 1.0 + self.rng.normal(0.0, self.noise_fraction)
            output_power_w = max(0.0, state.setpoint_w * noise)
        else:
            output_power_w = 0.0

        return LaserChannelInfo(
            port=self.port,
            box_id=self.box_id,
            channel=state.channel,
            idn=state.idn or f"Coherent OBIS Emulator {state.wavelength_nm:.0f} nm",
            wavelength_nm=float(state.wavelength_nm),
            nominal_power_w=float(state.nominal_power_w),
            min_setpoint_w=float(state.min_setpoint_w),
            max_setpoint_w=float(state.max_setpoint_w),
            setpoint_w=float(state.setpoint_w),
            output_power_w=float(output_power_w),
            enabled=state.enabled,
            cdrh_delay_enabled=state.cdrh_delay_enabled,
        )

    def set_power_w(self, channel: int, power_w: float) -> None:
        ch = int(channel)
        p = float(power_w)

        if ch not in self._channels:
            raise KeyError(f"Emulated OBIS {self.port} has no channel {ch}")

        state = self._channels[ch]

        if not math.isfinite(p):
            raise ValueError(f"Invalid power setpoint: {power_w!r}")

        if p < state.min_setpoint_w or p > state.max_setpoint_w:
            raise ValueError(
                f"Power setpoint {p:.6e} W is outside emulated laser range "
                f"[{state.min_setpoint_w:.6e}, {state.max_setpoint_w:.6e}] W"
            )

        state.setpoint_w = p

    def get_power_setpoint_w(self, channel: int) -> float:
        ch = int(channel)

        if ch not in self._channels:
            raise KeyError(f"Emulated OBIS {self.port} has no channel {ch}")

        return float(self._channels[ch].setpoint_w)

    def set_enabled(self, channel: int, enabled: bool) -> None:
        ch = int(channel)

        if ch not in self._channels:
            raise KeyError(f"Emulated OBIS {self.port} has no channel {ch}")

        self._channels[ch].enabled = (
            LaserEmissionState.ON if enabled else LaserEmissionState.OFF
        )

    def get_enabled(self, channel: int) -> LaserEmissionState:
        ch = int(channel)

        if ch not in self._channels:
            raise KeyError(f"Emulated OBIS {self.port} has no channel {ch}")

        return self._channels[ch].enabled

    def disable_all(self, max_channels: int = 5) -> None:
        for state in self._channels.values():
            state.enabled = LaserEmissionState.OFF

    def set_cdrh_delay(self, channel: int, enabled: bool) -> str:
        ch = int(channel)
        self._channels[ch].cdrh_delay_enabled = bool(enabled)
        return f"SYSTem{ch}:CDRH {'ON' if enabled else 'OFF'}"

    def get_cdrh_delay(self, channel: int) -> bool:
        ch = int(channel)
        return bool(self._channels[ch].cdrh_delay_enabled)


def make_emulated_obis_boxes() -> list[EmulatedObisBox]:
    """
    Creates two emulated laser boxes matching your discovered COM3/COM5 pattern.
    Adjust wavelengths/powers to match your actual installed lasers.
    """

    box1 = EmulatedObisBox(
        "COM3",
        serial="EMU-LB-003",
        channels=[
            _EmulatedLaserState(
                channel=0,
                wavelength_nm=405.0,
                nominal_power_w=0.100,
                min_setpoint_w=0.0005,
                max_setpoint_w=0.100,
                setpoint_w=0.001,
            ),
            _EmulatedLaserState(
                channel=1,
                wavelength_nm=488.0,
                nominal_power_w=0.150,
                min_setpoint_w=0.0005,
                max_setpoint_w=0.150,
                setpoint_w=0.001,
            ),
            _EmulatedLaserState(
                channel=2,
                wavelength_nm=561.0,
                nominal_power_w=0.100,
                min_setpoint_w=0.0005,
                max_setpoint_w=0.100,
                setpoint_w=0.001,
            ),
        ],
    )

    box2 = EmulatedObisBox(
        "COM5",
        serial="EMU-LB-005",
        channels=[
            _EmulatedLaserState(
                channel=0,
                wavelength_nm=640.0,
                nominal_power_w=0.140,
                min_setpoint_w=0.0005,
                max_setpoint_w=0.140,
                setpoint_w=0.001,
            ),
            _EmulatedLaserState(
                channel=1,
                wavelength_nm=730.0,
                nominal_power_w=0.080,
                min_setpoint_w=0.0005,
                max_setpoint_w=0.080,
                setpoint_w=0.001,
            ),
        ],
    )

    return [box1, box2]


