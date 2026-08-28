import numpy as np

from core.records import PowerSnapshot, SpectrumRecord
from validation.power_status import decode_newport_status_word, newport_status_valid


def test_missing_power_snapshot_has_no_finite_measurement() -> None:
    assert not PowerSnapshot.missing().has_finite_power()
    assert not PowerSnapshot([float("nan")], [], -1).has_finite_power()
    assert PowerSnapshot([float("nan"), 1.0e-3], [0, 0], 0).has_finite_power()


def test_spectrum_without_power_produces_no_finite_mean_snapshot() -> None:
    missing = PowerSnapshot.missing()
    record = SpectrumRecord(
        timestamp_utc="2026-08-27T00:00:00Z",
        timestamp_s=0.0,
        wavelengths_nm=np.asarray([500.0]),
        intensities_counts=np.asarray([1.0]),
        p_before=missing,
        p_after=PowerSnapshot.missing(),
        integration_ms=10,
        averages=1,
        boxcar_width=1,
        correct_dark=True,
        correct_nonlinearity=True,
        field_value=0.0,
    )

    assert not record.mean_power_snapshot().has_finite_power()
    assert np.isnan(record.mean_power_w(0))


def test_decodes_valid_status_word() -> None:
    decoded = decode_newport_status_word(0x118)
    assert decoded.detector_present
    assert not decoded.range_changing_or_unsettled
    assert not decoded.detector_saturated
    assert not decoded.overrange


def test_hex_string_and_integer_are_equivalent() -> None:
    assert decode_newport_status_word("118") == decode_newport_status_word(0x118)


def test_rejects_range_change() -> None:
    valid, reason = newport_status_valid(0x11C)
    assert not valid
    assert "range changing" in reason
