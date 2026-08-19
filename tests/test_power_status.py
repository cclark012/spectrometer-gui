from validation.power_status import decode_newport_status_word, newport_status_valid


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
