from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


@dataclass(frozen=True, slots=True)
class LaserCalibrationPoint:
    timestamp_utc: str
    port: str
    box_id: str
    channel: int
    wavelength_nm: float
    setpoint_w: float
    measured_power_mean_w: float
    measured_power_std_w: float
    n_reads: int
    filter_state: str = "none"


class LaserEmissionState(StrEnum):
    UNKNOWN = "unknown"
    ON = "on"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class LaserChannelInfo:
    port: str
    box_id: str
    channel: int

    idn: str = ""
    wavelength_nm: float = float("nan")
    nominal_power_w: float = float("nan")

    min_setpoint_w: float = float("nan")
    max_setpoint_w: float = float("nan")
    setpoint_w: float = float("nan")

    output_power_w: float = float("nan")
    enabled: LaserEmissionState = LaserEmissionState.UNKNOWN
    cdrh_delay_enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class PowerScanPoint:
    index: int
    requested_power_w: float
    requested_basis: Literal["setpoint", "expected_actual"]
    setpoint_w: float
    expected_actual_power_w: float
    filter_state: str = "none"
