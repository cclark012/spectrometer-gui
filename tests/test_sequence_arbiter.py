from __future__ import annotations

import pytest

from core.sequence_arbiter import SequenceArbiter


def test_claim_is_exclusive_and_idempotent() -> None:
    arbiter = SequenceArbiter()

    assert arbiter.claim("gated")
    assert arbiter.claim("gated")
    assert not arbiter.claim("power_scan")
    assert arbiter.owner == "gated"
    assert arbiter.automated


def test_only_owner_can_release() -> None:
    arbiter = SequenceArbiter()
    arbiter.claim("live")

    assert not arbiter.release("manual")
    assert arbiter.owner == "live"
    assert arbiter.release("live")
    assert not arbiter.active


def test_unknown_owner_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown sequence owner"):
        SequenceArbiter().claim("mystery")
