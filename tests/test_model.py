"""Tests for tokenifier.model."""
from datetime import datetime, timezone

import pytest

from tokenifier.model import Turn, Usage


def test_usage_with_only_required_fields():
    u = Usage(input_tokens=100, output_tokens=50)
    assert u.input_tokens == 100
    assert u.output_tokens == 50
    # Optional cache fields default to 0.
    assert u.cache_creation_input_tokens == 0
    assert u.cache_read_input_tokens == 0


def test_usage_allows_extras():
    # Recon note finding: Claude JSONLs carry undocumented sub-keys.
    # Pydantic must not reject them.
    u = Usage(
        input_tokens=100,
        output_tokens=50,
        service_tier="standard",
        cache_creation={"ephemeral_1h_input_tokens": 50, "ephemeral_5m_input_tokens": 0},
        inference_geo="us",
    )
    assert u.input_tokens == 100


def test_turn_input_total_sums_three_input_fields():
    turn = Turn(
        message_id="msg_1",
        timestamp=datetime.now(timezone.utc),
        model="claude-opus-4-7",
        usage=Usage(
            input_tokens=1000,
            output_tokens=500,
            cache_creation_input_tokens=2000,
            cache_read_input_tokens=4000,
        ),
    )
    # input_total = input + cache_creation + cache_read = 1000 + 2000 + 4000 = 7000
    assert turn.input_total == 7000


def test_turn_input_total_handles_zero_caches():
    turn = Turn(
        message_id="msg_1",
        timestamp=datetime.now(timezone.utc),
        model="claude-opus-4-7",
        usage=Usage(input_tokens=500, output_tokens=100),
    )
    assert turn.input_total == 500


def test_turn_defaults_for_optional_flags():
    turn = Turn(
        message_id="msg_1",
        timestamp=datetime.now(timezone.utc),
        model="claude-opus-4-7",
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    assert turn.is_sidechain is False
    assert turn.is_error is False


def test_turn_requires_timestamp():
    with pytest.raises(Exception):
        Turn(message_id="msg_1", model="x", usage=Usage(input_tokens=0, output_tokens=0))
