from __future__ import annotations

import math
from dataclasses import dataclass, field

from core.records import PowerSnapshot


@dataclass(slots=True)
class WrongPowerChannelTracker:
    """Detect persistent signal on Newport channels not associated with spectra."""

    _counts: dict[int, int] = field(default_factory=dict)
    _warned: set[int] = field(default_factory=set)
    _selection: tuple[int, ...] = ()

    def reset(self) -> None:
        self._counts.clear()
        self._warned.clear()
        self._selection = ()

    def evaluate(
        self,
        snapshot: PowerSnapshot,
        *,
        selected_channels: tuple[int, ...],
        threshold_w: float,
        consecutive: int,
    ) -> tuple[int, ...]:
        normalized_selection = tuple(
            sorted({int(channel) for channel in selected_channels})
        )
        if normalized_selection != self._selection:
            self._counts.clear()
            self._warned.clear()
            self._selection = normalized_selection
        selected = set(normalized_selection)
        threshold = max(0.0, float(threshold_w))
        required = max(1, int(consecutive))
        newly_warned: list[int] = []
        for channel, raw_value in enumerate(snapshot.powers_w):
            if channel in selected:
                self._counts.pop(channel, None)
                self._warned.discard(channel)
                continue
            value = float(raw_value)
            if math.isfinite(value) and value > threshold:
                count = self._counts.get(channel, 0) + 1
                self._counts[channel] = count
                if count >= required and channel not in self._warned:
                    self._warned.add(channel)
                    newly_warned.append(channel)
            else:
                self._counts.pop(channel, None)
                self._warned.discard(channel)
        return tuple(newly_warned)
