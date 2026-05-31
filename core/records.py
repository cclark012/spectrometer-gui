from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class BackgroundSpectrum:
    timestamp_utc: str
    wavelengths_nm: np.ndarray
    counts_per_s: np.ndarray

    integration_ms: int
    averages: int
    correct_dark: bool
    correct_nonlinearity: bool
    averaging_mode: str = "software"


@dataclass(slots=True)
class MonitorTracePoint:
    timestamp_utc: str
    elapsed_s: float

    field_mT: float

    power_ch1_W: float
    power_ch2_W: float

    intensity_target_counts: float
    intensity_target_nm: float

    integrated_range_counts_nm: float
    integration_start_nm: float
    integration_stop_nm: float

    total_integrated_counts_nm: float

    peak_intensity_counts: float
    peak_wavelength_nm: float

    signal_max_counts: float
    signal_mean_counts: float


@dataclass
class PowerSnapshot:
    powers_w: list[float]
    pm_status: list[int]
    command_status: int = 0


@dataclass(frozen=True)
class PowerTracePoint:
    timestamp_utc: str
    elapsed_s: float
    source: str  # "poll" or "spectrum"
    powers_w: list[float]
    pm_status: list[int]
    command_status: int


@dataclass
class SpectrometerCapabilities:
    model: str = ""
    serial_number: str = ""
    pixels: int = 0
    max_intensity: float = float("nan")
    integration_time_min_us: int = 0
    integration_time_max_us: int = 0
    features: list[str] = field(default_factory=list)
    feature_methods: dict[str, list[str]] = field(default_factory=dict)
    tec_supported: bool = False
    device_averaging_supported: bool = False

@dataclass
class SpectrometerInfo:
    name: str = "unknown"
    serial_number: str = ""
    max_intensity: float = float("nan")
    emulated: bool = False


@dataclass
class SpectrumRecord:
    timestamp_utc: str
    timestamp_s: float
    wavelengths_nm: np.ndarray
    intensities_counts: np.ndarray
    p_before: PowerSnapshot
    p_after: PowerSnapshot
    integration_ms: int
    averages: int
    boxcar_width: int
    correct_dark: bool
    correct_nonlinearity: bool
    field_value: float
    signal_max_counts: float = float("nan")
    spectrometer_max_intensity: float = float("nan")
    run_identifier: str = ""
    notes: str = ""

    scan_active: bool = False
    scan_index: int = -1
    scan_count: int = 0
    scan_basis: str = ""
    scan_spacing: str = ""

    laser_port: str = ""
    laser_box_id: str = ""
    laser_channel: int = -1
    laser_wavelength_nm: float = float("nan")
    laser_setpoint_w: float = float("nan")
    requested_power_w: float = float("nan")
    expected_actual_power_w: float = float("nan")
    filter_state: str = "none"

    averaging_mode: str = "software"
    device_averaging_used: bool = False

    background_subtracted: bool = False
    background_timestamp_utc: str = ""
    background_integration_ms: int = 0

    def mean_power_w(self, channel_index: int = 0) -> float:
        if len(self.p_before.powers_w) <= channel_index:
            return float("nan")
        if len(self.p_after.powers_w) <= channel_index:
            return float("nan")

        return 0.5 * (
            float(self.p_before.powers_w[channel_index])
            + float(self.p_after.powers_w[channel_index])
        )


    def integration_time_s(self) -> float:
        return max(float(self.integration_ms) * 1.0e-3, 1.0e-12)


    def intensities_counts_per_s(self) -> np.ndarray:
        # Your acquisition currently averages frames, not sums them.
        # Therefore divide by one frame's integration time, not by averages.
        return self.intensities_counts / self.integration_time_s()
