from __future__ import annotations

from dataclasses import dataclass


SEQUENCE_LABELS: dict[str, str] = {
    "manual": "manual acquisition",
    "live": "live acquisition",
    "background": "background capture",
    "power_scan": "power scan",
    "calibration": "calibration scan",
    "gated": "gated acquisition",
    "auto_tune": "automatic acquisition tuning",
}

AUTOMATED_SEQUENCE_OWNERS = frozenset(
    {"power_scan", "calibration", "gated", "auto_tune"}
)


@dataclass(slots=True)
class SequenceArbiter:
    """Own the single instrument sequence allowed to run at a time.

    Claims are idempotent for the current owner so a coordinator can retain its
    lease across several hardware requests. A different owner can never release
    that lease accidentally.
    """

    _owner: str | None = None

    @property
    def owner(self) -> str | None:
        return self._owner

    @property
    def active(self) -> bool:
        return self._owner is not None

    @property
    def automated(self) -> bool:
        return self._owner in AUTOMATED_SEQUENCE_OWNERS

    @property
    def label(self) -> str | None:
        if self._owner is None:
            return None
        return SEQUENCE_LABELS[self._owner]

    def claim(self, owner: str) -> bool:
        owner = self._validate_owner(owner)
        if self._owner is None:
            self._owner = owner
            return True
        return self._owner == owner

    def release(self, owner: str) -> bool:
        owner = self._validate_owner(owner)
        if self._owner != owner:
            return False
        self._owner = None
        return True

    @staticmethod
    def _validate_owner(owner: str) -> str:
        value = str(owner)
        if value not in SEQUENCE_LABELS:
            raise ValueError(f"Unknown sequence owner: {owner!r}")
        return value
