"""Bounded ordinary scans. No laser transitions or gated timing semantics."""
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class ParameterScanPlan:
    kind: str
    values: tuple[float, ...]
    repeats: int = 1
    settle_s: float = 0.0
    temperature_source: str = 'ccd'
    tolerance: float = 0.2
    dwell_s: float = 5.0
    timeout_s: float = 600.0
    max_age_s: float = 5.0
    restore: bool = True
    lasers_off_at_end: bool = True

    def __post_init__(self):
        if self.kind not in {'exposure', 'wavelength', 'time', 'temperature'}:
            raise ValueError('Unknown scan parameter')
        if not self.values or len(self.values) > 10000:
            raise ValueError('Provide 1–10000 finite scan points')
        if self.repeats < 1 or len(self.values) * self.repeats > 10000:
            raise ValueError('Scan must contain at most 10000 acquisitions')
        if not all((math.isfinite(v) for v in self.values)):
            raise ValueError('Scan values must be finite')
        if self.kind in {'exposure', 'wavelength'} and min(self.values) <= 0:
            raise ValueError('Exposure and wavelength must be positive')
        if self.kind == 'exposure' and any((v != int(v) for v in self.values)):
            raise ValueError('Exposure points must be whole milliseconds')
        if self.kind == 'time' and (min(self.values) < 0 or any((b <= a for a, b in zip(self.values, self.values[1:]))) or self.repeats != 1):
            raise ValueError('Time offsets must increase strictly, be nonnegative, and use one repeat')
        for name in ('settle_s', 'tolerance', 'dwell_s', 'timeout_s', 'max_age_s'):
            v = getattr(self, name)
            if not math.isfinite(v) or v < 0:
                raise ValueError(f'{name} must be finite and nonnegative')
        if self.timeout_s <= 0 or self.max_age_s <= 0:
            raise ValueError('Timeout and maximum reading age must be positive')
        if self.temperature_source not in {'ccd', 'lakeshore_A', 'lakeshore_B'}:
            raise ValueError('Unknown temperature source')
        if self.kind == 'temperature' and self.temperature_source != 'ccd' and (min(self.values) < 0):
            raise ValueError('Lake Shore targets are in kelvin and cannot be negative')

    @property
    def points(self):
        return tuple((v for v in self.values for _ in range(self.repeats)))

@dataclass(frozen=True)
class TemperatureReading:
    source: str
    value: float
    units: str
    sampled_s: float
    timestamp_utc: str
    status: int = 0
    identity: str = ''

    def fresh(self, now, max_age):
        return self.status == 0 and math.isfinite(self.value) and (0 <= now - self.sampled_s <= max_age)

class TemperatureGate:
    """Require distinct, fresh, in-band samples throughout the dwell interval."""

    def __init__(self):
        self.since = None
        self.last_sample = None

    def update(self, reading, *, target, now, tolerance, dwell, max_age):
        if reading is None or not reading.fresh(now, max_age) or abs(reading.value - target) > tolerance:
            self.since = self.last_sample = None
            return False
        if self.last_sample == reading.sampled_s:
            return False
        if self.last_sample is not None and reading.sampled_s - self.last_sample > max_age:
            self.since = None
        self.last_sample = reading.sampled_s
        if self.since is None:
            self.since = reading.sampled_s
        return reading.sampled_s - self.since >= dwell

def generate_scan_values(start: float, stop: float, count: int, spacing: str) -> tuple[float, ...]:
    """Generate bounded points; final plan validation checks parameter-specific constraints."""
    import numpy as np
    if not 1 <= count <= 10000 or not all((math.isfinite(v) for v in (start, stop))):
        raise ValueError('Use finite endpoints and 1–10000 points')
    if spacing == 'linear':
        values = np.linspace(start, stop, count)
    elif spacing == 'log':
        if start <= 0 or stop <= 0:
            raise ValueError('Logarithmic endpoints must be positive')
        values = np.geomspace(start, stop, count)
    else:
        raise ValueError('Unknown spacing')
    return tuple((float(v) for v in values))
