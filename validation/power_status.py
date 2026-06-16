from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NewportChannelStatus:
    raw: int
    units_code: int
    range_code: int
    detector_present: bool
    range_changing_or_unsettled: bool
    detector_saturated: bool
    overrange: bool


def decode_newport_status_word(status: int | str) -> NewportChannelStatus:
    if isinstance(status, str):
        text = status.strip()

        if text.lower().startswith("0x"):
            raw = int(text, 16)
        else:
            # Newport command manual describes PM:PWS status as hexadecimal.
            raw = int(text, 16)
    else:
        raw = int(status)

    return NewportChannelStatus(
        raw=raw,
        units_code=(raw >> 7) & 0b111,
        range_code=(raw >> 4) & 0b111,
        detector_present=bool(raw & (1 << 3)),
        range_changing_or_unsettled=bool(raw & (1 << 2)),
        detector_saturated=bool(raw & (1 << 1)),
        overrange=bool(raw & (1 << 0)),
    )


def newport_status_valid(
    status: int | str,
    *,
    require_detector_present: bool = True,
    reject_range_changing: bool = True,
    reject_saturated: bool = True,
    reject_overrange: bool = True,
) -> tuple[bool, str]:
    decoded = decode_newport_status_word(status)

    if require_detector_present and not decoded.detector_present:
        return False, f"detector not present; status=0x{decoded.raw:X}"

    if reject_range_changing and decoded.range_changing_or_unsettled:
        return False, f"range changing/unsettled; status=0x{decoded.raw:X}"

    if reject_saturated and decoded.detector_saturated:
        return False, f"detector saturated; status=0x{decoded.raw:X}"

    if reject_overrange and decoded.overrange:
        return False, f"overrange; status=0x{decoded.raw:X}"

    return True, ""
