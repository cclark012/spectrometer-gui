from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from core.records import SpectrumRecord


@dataclass(frozen=True, slots=True)
class TimingStatistics:
    mean_ms: float = float("nan")
    std_ms: float = float("nan")
    minimum_ms: float = float("nan")
    maximum_ms: float = float("nan")
    median_ms: float = float("nan")
    p95_ms: float = float("nan")
    p99_ms: float = float("nan")


@dataclass(frozen=True, slots=True)
class GatedAverageTrace:
    label: str
    laser_state: str
    requested_delay_ms: int
    sample_count: int
    mean_counts: np.ndarray
    std_counts: np.ndarray
    request_timing: TimingStatistics
    acquisition_start_timing: TimingStatistics
    acquisition_midpoint_timing: TimingStatistics
    acquisition_end_timing: TimingStatistics
    exposure_start_timing: TimingStatistics = TimingStatistics()
    exposure_midpoint_timing: TimingStatistics = TimingStatistics()
    exposure_end_timing: TimingStatistics = TimingStatistics()
    exposure_uncertainty: TimingStatistics = TimingStatistics()
    timing_error: TimingStatistics = TimingStatistics()
    mean_power_w: tuple[float, ...] = ()
    std_power_w: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class GatedSeriesRecord:
    sequence_id: str
    mode: str
    timestamp_utc: str
    wavelengths_nm: np.ndarray
    traces: tuple[GatedAverageTrace, ...]
    integration_ms: int
    detector_averages: int
    field_value_mT: float
    laser_port: str
    laser_box_id: str
    laser_channel: int
    laser_wavelength_nm: float
    timing_evaluated_count: int = 0
    timing_rejected_count: int = 0
    timing_guard_method: str = "off"


@dataclass(slots=True)
class _Group:
    label: str
    laser_state: str
    requested_delay_ms: int
    count: int = 0
    mean: np.ndarray | None = None
    m2: np.ndarray | None = None
    request_ms: list[float] = field(default_factory=list)
    start_ms: list[float] = field(default_factory=list)
    midpoint_ms: list[float] = field(default_factory=list)
    end_ms: list[float] = field(default_factory=list)
    exposure_start_ms: list[float] = field(default_factory=list)
    exposure_midpoint_ms: list[float] = field(default_factory=list)
    exposure_end_ms: list[float] = field(default_factory=list)
    exposure_uncertainty_ms: list[float] = field(default_factory=list)
    timing_error_ms: list[float] = field(default_factory=list)
    power_w: list[list[float]] = field(default_factory=list)

    def add(self, record: SpectrumRecord) -> None:
        values = np.asarray(record.intensities_counts, dtype=float)
        self.count += 1
        if self.mean is None or self.m2 is None:
            self.mean = values.copy()
            self.m2 = np.zeros_like(values, dtype=float)
        else:
            delta = values - self.mean
            self.mean += delta / float(self.count)
            self.m2 += delta * (values - self.mean)

        gated = record.gated
        if gated is None:
            return
        self.request_ms.append(float(gated.request_elapsed_since_transition_ms))
        self.start_ms.append(float(gated.acquisition_call_start_elapsed_ms))
        self.midpoint_ms.append(float(gated.acquisition_call_midpoint_elapsed_ms))
        self.end_ms.append(float(gated.acquisition_call_end_elapsed_ms))
        self.exposure_start_ms.append(float(gated.exposure_window_start_elapsed_ms))
        self.exposure_midpoint_ms.append(
            float(gated.exposure_midpoint_estimate_elapsed_ms)
        )
        self.exposure_end_ms.append(float(gated.exposure_window_end_elapsed_ms))
        self.exposure_uncertainty_ms.append(
            float(gated.exposure_timing_uncertainty_ms)
        )
        self.timing_error_ms.append(float(gated.timing_error_ms))
        self.power_w.append(
            [float(value) for value in record.mean_power_snapshot().powers_w]
        )


def _timing_statistics(values: list[float]) -> TimingStatistics:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return TimingStatistics()
    return TimingStatistics(
        mean_ms=float(np.mean(finite)),
        std_ms=float(np.std(finite, ddof=1)) if finite.size > 1 else 0.0,
        minimum_ms=float(np.min(finite)),
        maximum_ms=float(np.max(finite)),
        median_ms=float(np.median(finite)),
        p95_ms=float(np.percentile(finite, 95.0)),
        p99_ms=float(np.percentile(finite, 99.0)),
    )


def _power_statistics(samples: list[list[float]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    channel_count = max((len(sample) for sample in samples), default=0)
    means: list[float] = []
    deviations: list[float] = []
    for channel in range(channel_count):
        values = np.asarray(
            [sample[channel] for sample in samples if len(sample) > channel],
            dtype=float,
        )
        values = values[np.isfinite(values)]
        means.append(float(np.mean(values)) if values.size else float("nan"))
        if values.size > 1:
            deviations.append(float(np.std(values, ddof=1)))
        elif values.size == 1:
            deviations.append(0.0)
        else:
            deviations.append(float("nan"))
    return tuple(means), tuple(deviations)


class GatedSeriesAccumulator:
    """Incrementally average repeated gated frames without retaining every frame."""

    def __init__(self) -> None:
        self._sequence_id = ""
        self._mode = ""
        self._wavelengths_nm: np.ndarray | None = None
        self._groups: dict[tuple[str, str, int], _Group] = {}
        self._first_record: SpectrumRecord | None = None

    @property
    def sample_count(self) -> int:
        return sum(group.count for group in self._groups.values())

    def add(self, record: SpectrumRecord) -> None:
        gated = record.gated
        if gated is None or not gated.sequence_id:
            raise ValueError("A gated frame with a sequence ID is required.")

        wavelengths = np.asarray(record.wavelengths_nm, dtype=float)
        intensities = np.asarray(record.intensities_counts, dtype=float)
        if wavelengths.ndim != 1 or intensities.shape != wavelengths.shape:
            raise ValueError("Gated wavelength and intensity arrays must be matching 1-D arrays.")

        if self._wavelengths_nm is None:
            self._sequence_id = str(gated.sequence_id)
            self._mode = str(gated.mode)
            self._wavelengths_nm = wavelengths.copy()
            self._first_record = record
        else:
            if gated.sequence_id != self._sequence_id:
                raise ValueError("Cannot combine frames from different gated sequences.")
            if wavelengths.shape != self._wavelengths_nm.shape or not np.allclose(
                wavelengths,
                self._wavelengths_nm,
                rtol=0.0,
                atol=1.0e-9,
                equal_nan=False,
            ):
                raise ValueError("The wavelength grid changed during the gated sequence.")

        key = (str(gated.label), str(gated.laser_state), int(gated.requested_delay_ms))
        group = self._groups.get(key)
        if group is None:
            group = _Group(
                label=key[0],
                laser_state=key[1],
                requested_delay_ms=key[2],
            )
            self._groups[key] = group
        group.add(record)

    def finish(self) -> GatedSeriesRecord:
        if self._first_record is None or self._wavelengths_nm is None:
            raise ValueError("No gated frames were accumulated.")

        groups = list(self._groups.values())
        if self._mode in {"delayed_after_off", "interleaved_decay"}:
            groups.sort(key=lambda item: (item.requested_delay_ms, item.label))

        traces: list[GatedAverageTrace] = []
        for group in groups:
            if group.mean is None or group.m2 is None or group.count < 1:
                continue
            standard_deviation = (
                np.sqrt(group.m2 / float(group.count - 1))
                if group.count > 1
                else np.zeros_like(group.mean)
            )
            power_mean, power_std = _power_statistics(group.power_w)
            traces.append(
                GatedAverageTrace(
                    label=group.label,
                    laser_state=group.laser_state,
                    requested_delay_ms=group.requested_delay_ms,
                    sample_count=group.count,
                    mean_counts=group.mean.copy(),
                    std_counts=standard_deviation,
                    request_timing=_timing_statistics(group.request_ms),
                    acquisition_start_timing=_timing_statistics(group.start_ms),
                    acquisition_midpoint_timing=_timing_statistics(group.midpoint_ms),
                    acquisition_end_timing=_timing_statistics(group.end_ms),
                    exposure_start_timing=_timing_statistics(group.exposure_start_ms),
                    exposure_midpoint_timing=_timing_statistics(
                        group.exposure_midpoint_ms
                    ),
                    exposure_end_timing=_timing_statistics(group.exposure_end_ms),
                    exposure_uncertainty=_timing_statistics(
                        group.exposure_uncertainty_ms
                    ),
                    timing_error=_timing_statistics(group.timing_error_ms),
                    mean_power_w=power_mean,
                    std_power_w=power_std,
                )
            )

        first = self._first_record
        return GatedSeriesRecord(
            sequence_id=self._sequence_id,
            mode=self._mode,
            timestamp_utc=str(first.timestamp_utc),
            wavelengths_nm=self._wavelengths_nm.copy(),
            traces=tuple(traces),
            integration_ms=int(first.integration_ms),
            detector_averages=int(first.averages),
            field_value_mT=float(first.field_value) if first.field_value is not None else None,
            laser_port=str(first.laser_port),
            laser_box_id=str(first.laser_box_id),
            laser_channel=int(first.laser_channel),
            laser_wavelength_nm=float(first.laser_wavelength_nm),
        )
