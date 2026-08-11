from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from core.laser_models import LaserChannelInfo, LaserEmissionState


@dataclass(slots=True)
class EmulatedLaserState:
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
    def __init__(
        self,
        port: str,
        *,
        serial: str,
        channels: list[EmulatedLaserState],
        noise_fraction: float = 0.002,
    ) -> None:
        self.port = str(port)
        self.serial = str(serial)
        self.box_id = f"EMULATED OBIS Laser Box {self.serial}"
        self.noise_fraction = float(noise_fraction)
        self._rng = np.random.default_rng(abs(hash((self.port, self.serial))) % (2**32))
        self._channels = {int(channel.channel): channel for channel in channels}

    def close(self) -> None:
        pass

    def discover_channels(self, max_channels: int = 5) -> list[LaserChannelInfo]:
        del max_channels
        return [self.read_channel_info(channel) for channel in sorted(self._channels)]

    def read_channel_info(self, channel: int) -> LaserChannelInfo:
        state = self._get_state(channel)
        if state.enabled == LaserEmissionState.ON:
            noise = 1.0 + self._rng.normal(0.0, self.noise_fraction)
            output_power_w = max(0.0, state.setpoint_w * noise)
        else:
            output_power_w = 0.0

        return LaserChannelInfo(
            port=self.port,
            box_id=self.box_id,
            channel=state.channel,
            idn=state.idn or f"Coherent OBIS Emulator {state.wavelength_nm:.0f} nm",
            wavelength_nm=state.wavelength_nm,
            nominal_power_w=state.nominal_power_w,
            min_setpoint_w=state.min_setpoint_w,
            max_setpoint_w=state.max_setpoint_w,
            setpoint_w=state.setpoint_w,
            output_power_w=output_power_w,
            enabled=state.enabled,
            cdrh_delay_enabled=state.cdrh_delay_enabled,
        )

    def _get_state(self, channel: int) -> EmulatedLaserState:
        channel = int(channel)
        try:
            return self._channels[channel]
        except KeyError as exc:
            raise KeyError(f"Emulated OBIS {self.port} has no channel {channel}") from exc

    def set_power_w(self, channel: int, power_w: float) -> None:
        state = self._get_state(channel)
        power_w = float(power_w)
        if not math.isfinite(power_w):
            raise ValueError(f"Invalid power setpoint: {power_w!r}")
        if not state.min_setpoint_w <= power_w <= state.max_setpoint_w:
            raise ValueError(
                f"Power setpoint {power_w:.6e} W is outside emulated laser range "
                f"[{state.min_setpoint_w:.6e}, {state.max_setpoint_w:.6e}] W"
            )
        state.setpoint_w = power_w

    def get_power_setpoint_w(self, channel: int) -> float:
        return float(self._get_state(channel).setpoint_w)

    def set_enabled(self, channel: int, enabled: bool) -> None:
        self._get_state(channel).enabled = (
            LaserEmissionState.ON if enabled else LaserEmissionState.OFF
        )

    def get_enabled(self, channel: int) -> LaserEmissionState:
        return self._get_state(channel).enabled

    def disable_all(self, max_channels: int = 5) -> None:
        del max_channels
        for state in self._channels.values():
            state.enabled = LaserEmissionState.OFF

    def set_cdrh_delay(self, channel: int, enabled: bool) -> str:
        state = self._get_state(channel)
        state.cdrh_delay_enabled = bool(enabled)
        return f"SYSTem{state.channel}:CDRH {'ON' if enabled else 'OFF'}"

    def get_cdrh_delay(self, channel: int) -> bool:
        return bool(self._get_state(channel).cdrh_delay_enabled)


def make_emulated_obis_boxes() -> list[EmulatedObisBox]:
    return [
        EmulatedObisBox(
            "COM3",
            serial="EMU-LB-003",
            channels=[
                EmulatedLaserState(1, 405.0, 0.100, 0.0005, 0.100, 0.001),
                EmulatedLaserState(2, 488.0, 0.150, 0.0005, 0.150, 0.001),
                EmulatedLaserState(3, 532.0, 0.100, 0.0005, 0.100, 0.001),
            ],
        ),
        EmulatedObisBox(
            "COM5",
            serial="EMU-LB-005",
            channels=[
                EmulatedLaserState(1, 660.0, 0.140, 0.0005, 0.140, 0.001),
                EmulatedLaserState(2, 808.0, 0.080, 0.0005, 0.080, 0.001),
            ],
        ),
    ]
