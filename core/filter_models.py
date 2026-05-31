from __future__ import annotations

from dataclasses import dataclass
from math import pow


@dataclass(frozen=True, slots=True)
class FilterPosition:
    label: str
    optical_density: float = 0.0

    @property
    def transmission(self) -> float:
        return pow(10.0, -float(self.optical_density))


@dataclass(frozen=True, slots=True)
class FilterWheel:
    name: str
    positions: tuple[FilterPosition, ...]


@dataclass(frozen=True, slots=True)
class FilterState:
    positions: tuple[tuple[str, str], ...]
    optical_density: float
    transmission: float

    @property
    def label(self) -> str:
        return ", ".join(f"{wheel}:{position}" for wheel, position in self.positions)


@dataclass(frozen=True, slots=True)
class FilterPlanStep:
    index: int
    target_power_w: float
    filter_state: FilterState
    required_setpoint_w: float
    expected_actual_power_w: float
