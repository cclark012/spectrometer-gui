from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.gated_acquisition import GatedFrameMetadata


@dataclass
class AcquisitionSettings:
    integration_ms: int
    averages: int
    boxcar_width: int
    correct_dark: bool
    correct_nonlinearity: bool
    field_value: float
    run_identifier: str = ""
    notes: str = ""

    averaging_mode: str = "software"  # "software" or "device"
    subtract_background: bool = False
    gated: GatedFrameMetadata | None = None

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


@dataclass
class DeviceConfig:
    emulate: bool
    fallback_emulator: bool
    newport_dll: Path | None
    power_channel: int

    emulate_lasers: bool = False
    laser_fallback_emulator: bool = False
    obis_ports: list[str] | None = None


@dataclass
class DisplaySettings:
    live_acquisition_gap_ms: int = 0
    spectrum_redraw_interval_ms: int = 200
    monitor_redraw_interval_ms: int = 200
    power_redraw_interval_ms: int = 200
    performance_enabled: bool = True
    performance_report_interval_ms: int = 1000
    event_loop_probe_interval_ms: int = 250
    performance_rate_window_s: float = 5.0
    theme_name: str = "visual_studio_dark"


@dataclass
class FileNameSettings:
    save_directory: Path = field(default_factory=lambda: Path("data"))
    base_name: str = "spectrum"
    run_identifier: str = ""
    notes: str = ""

    include_date: bool = True
    include_time: bool = True
    include_power: bool = False
    include_field: bool = True
    include_run_identifier: bool = True
    include_enumeration: bool = True

    autosave_spectra: bool = False
    extension: str = ".csv"


@dataclass
class PlotStyleSettings:
    spectrum_color: str = "y"
    monitor_color: str = "c"
    power_color: str = "g"

    spectrum_line_width: float = 2.0
    monitor_line_width: float = 2.0
    power_line_width: float = 2.0

    spectrum_show_line: bool = True
    monitor_show_line: bool = True
    power_show_line: bool = True

    spectrum_show_symbols: bool = False
    monitor_show_symbols: bool = True
    power_show_symbols: bool = False

    symbol: str = "o"
    symbol_size: int = 6

    font_size: int = 10

    spectrum_auto_range: bool = True
    spectrum_x_min: float = 250.0
    spectrum_x_max: float = 1050.0
    spectrum_y_min: float = 0.0
    spectrum_y_max: float = 65535.0

    monitor_auto_range: bool = True
    power_auto_range: bool = True


@dataclass
class PowerMonitorSettings:
    mode: str = "live"  # "live" or "spectra_only"

    @property
    def live_polling_enabled(self) -> bool:
        return self.mode == "live"

    append_spectrum_power: bool = True

    max_points: int = 600
    interval_ms: int = 1000

    validation_enabled: bool = True
    max_valid_power_w: float = 0.200  # 200 mW
    reject_negative_power: bool = False
    invalid_power_retries: int = 3
    invalid_power_retry_delay_s: float = 0.10

    validate_status_words: bool = True
    required_power_channels: tuple[int, ...] = (0,)
    require_detector_present: bool = True
    reject_range_changing: bool = True
    reject_detector_saturated: bool = True
    reject_overrange: bool = True


@dataclass
class SignalWarningSettings:
    enabled: bool = True

    use_spectrometer_max: bool = True
    fraction_of_spectrometer_max: float = 0.95

    absolute_threshold_counts: float = 60000.0

    popup_enabled: bool = True
    popup_cooldown_s: float = 30.0


@dataclass
class SNRSettings:
    enabled: bool = False
    signal_start_nm: float = 400.0
    signal_stop_nm: float = 750.0
    noise1_start_nm: float = 900.0
    noise1_stop_nm: float = 1100.0

    use_noise2: bool = False
    noise2_start_nm: float = 360.0
    noise2_stop_nm: float = 420.0

    baseline_order: int = 1
    minimum_noise_pixels: int = 20
    peak_percentile: float = 99.5
    update_every_n_spectra: int = 1

    target_snr: float = 100.0
    target_peak_fraction: float = 0.75
    recommendation_metric: str = "integrated"

    auto_suggest_enabled: bool = False
    auto_adjust_max_iterations: int = 3
    auto_adjust_tolerance_fraction: float = 0.10

    maximum_integration_ms: int = 60_000
    maximum_averages: int = 100
    maximum_total_acquisition_s: float = 60.0
