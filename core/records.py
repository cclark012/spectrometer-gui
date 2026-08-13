from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from core.gated_acquisition import GatedFrameMetadata
from core.snr_records import SNRMetrics

FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class InstrumentConnectionState:
    key: str
    connected: bool
    emulated: bool = False
    description: str = ""
    error: str = ""


@dataclass(slots=True)
class BackgroundSpectrum:
    timestamp_utc: str
    wavelengths_nm: FloatArray
    counts_per_s: FloatArray
    integration_ms: int
    averages: int
    correct_dark: bool
    correct_nonlinearity: bool
    averaging_mode: str = "software"


@dataclass(frozen=True, slots=True)
class SpectralAcquisition:
    """Raw result returned by a spectrometer adapter."""

    wavelengths_nm: FloatArray
    intensities_counts: FloatArray
    signal_max_counts: float
    device_averaging_used: bool = False


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


@dataclass(slots=True)
class PowerSnapshot:
    powers_w: list[float]
    pm_status: list[int]
    command_status: int = 0

    @classmethod
    def missing(cls) -> "PowerSnapshot": # noqa
        return cls(powers_w=[], pm_status=[], command_status=-1)


@dataclass(frozen=True, slots=True)
class PowerTracePoint:
    timestamp_utc: str
    elapsed_s: float
    source: str  # "poll" or "spectrum_mean"
    powers_w: list[float]
    pm_status: list[int]
    command_status: int


@dataclass(slots=True)
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


@dataclass(slots=True)
class SpectrometerInfo:
    name: str = "unknown"
    serial_number: str = ""
    max_intensity: float = float("nan")
    emulated: bool = False


@dataclass(slots=True)
class SpectrumRecord:
    timestamp_utc: str
    timestamp_s: float
    wavelengths_nm: FloatArray
    intensities_counts: FloatArray
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
    snr: SNRMetrics | None = None
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
    gated: GatedFrameMetadata | None = None

    background_subtracted: bool = False
    background_timestamp_utc: str = ""
    background_integration_ms: int = 0

    def mean_power_w(self, channel_index: int = 0) -> float:
        if channel_index < 0:
            return float("nan")
        if len(self.p_before.powers_w) <= channel_index:
            return float("nan")
        if len(self.p_after.powers_w) <= channel_index:
            return float("nan")

        return 0.5 * (
            float(self.p_before.powers_w[channel_index])
            + float(self.p_after.powers_w[channel_index])
        )

    def mean_power_snapshot(self) -> PowerSnapshot:
        """Combine before/after readings for display and logging."""

        count = min(len(self.p_before.powers_w), len(self.p_after.powers_w))
        powers = [
            0.5 * (float(self.p_before.powers_w[index]) + float(self.p_after.powers_w[index]))
            for index in range(count)
        ]
        status_count = min(len(self.p_before.pm_status), len(self.p_after.pm_status))

        # Bits 0-3 are transient/error flags. Preserve those flags from either
        # reading, but keep the units/range fields from the later status word;
        # bitwise-ORing the complete words can manufacture an invalid range code.
        flag_mask = 0x0F
        statuses = []
        for index in range(status_count):
            before = int(self.p_before.pm_status[index])
            after = int(self.p_after.pm_status[index])
            statuses.append((after & ~flag_mask) | ((before | after) & flag_mask))
        return PowerSnapshot(
            powers_w=powers,
            pm_status=statuses,
            command_status=(
                int(self.p_before.command_status) | int(self.p_after.command_status)
            ),
        )

    def integration_time_s(self) -> float:
        value = float(self.integration_ms) * 1.0e-3
        return value if np.isfinite(value) and value > 0.0 else float("nan")

    def intensities_counts_per_s(self) -> FloatArray:
        # Frames are averaged rather than summed, so only one integration
        # interval belongs in the denominator. Loaded legacy spectra may not
        # contain an integration time; report NaN rather than an enormous,
        # misleading value in that case.
        integration_s = self.integration_time_s()
        if not np.isfinite(integration_s):
            return np.full_like(
                np.asarray(self.intensities_counts, dtype=float),
                np.nan,
                dtype=float,
            )
        return np.asarray(self.intensities_counts, dtype=float) / integration_s
