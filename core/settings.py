from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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
    mode: str = "live" # "live" or "spectra_only"

    polling_enabled: bool = True
    append_spectrum_power: bool = True

    max_points: int = 600
    interval_ms: int = 1000

    validation_enabled: bool = True
    max_valid_power_w: float = 0.200  # 100 mW
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
