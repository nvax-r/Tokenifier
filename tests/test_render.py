"""Tests for tokenifier.render."""
from datetime import datetime, timezone

import pytest
from rich.console import Console

from tokenifier.model import Talk, Turn, Usage
from tokenifier.render import render, BAR_FILL_CHAR, BAR_EMPTY_CHAR, _bar_width


def _make_turn(model="claude-opus-4-7", input_total=64_000, output=8_000, message_id="m1"):
    """Helper: build a Turn whose `input_total` resolves to the given total."""
    return Turn(
        message_id=message_id,
        timestamp=datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc),
        model=model,
        usage=Usage(
            input_tokens=input_total,
            output_tokens=output,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


def _render_to_text(turns_or_talks, width=80):
    """Render via a width-fixed recording console and return the text.

    Accepts a list of Turns (each wrapped in a single-Turn Talk) or a
    list of Talks directly.
    """
    if turns_or_talks and isinstance(turns_or_talks[0], Turn):
        talks = [Talk(turns=[t]) for t in turns_or_talks]
    else:
        talks = turns_or_talks
    console = Console(width=width, record=True)
    render(talks, console=console)
    return console.export_text()


# ---- Per-turn block ----

def test_renders_turn_header_with_model_and_window():
    turn = _make_turn()
    out = _render_to_text([turn])
    assert "opus-4-7" in out
    assert "200K" in out


def test_input_bar_fills_proportionally():
    # 64K / 200K = 32% → at width=80, bar_width=40, expect 12 fill chars.
    turn = _make_turn(input_total=64_000)
    out = _render_to_text([turn], width=80)
    bar_width = _bar_width(80)
    expected_fill = int(0.32 * bar_width)
    expected_empty = bar_width - expected_fill
    assert BAR_FILL_CHAR * expected_fill + BAR_EMPTY_CHAR * expected_empty in out


def test_danger_marker_appears_at_85_percent():
    # 178K / 200K = 89% — danger zone, should show ⚠.
    turn = _make_turn(input_total=178_000)
    out = _render_to_text([turn])
    assert "⚠" in out


def test_no_danger_marker_below_threshold():
    # 168K / 200K = 84% — below the 85% threshold.
    turn = _make_turn(input_total=168_000)
    out = _render_to_text([turn])
    assert "⚠" not in out


def test_output_line_shows_window_pct_and_cap_pct():
    # 8K output → 8K / 200K = 4% of window, 8K / 128K = 6.25% → 6% of cap.
    turn = _make_turn(input_total=64_000, output=8_000)
    out = _render_to_text([turn])
    assert "4%" in out  # of window
    assert "6%" in out  # of cap


def test_free_line_shows_headroom():
    # 200K - 64K input - 8K output = 128K free.
    turn = _make_turn(input_total=64_000, output=8_000)
    out = _render_to_text([turn])
    assert "128K" in out
    assert "headroom" in out


# ---- Model-boundary divider ----

def test_no_divider_for_single_turn():
    turn = _make_turn(model="claude-opus-4-7")
    out = _render_to_text([turn])
    assert "boundary:" not in out


def test_no_divider_when_models_match():
    a = _make_turn(model="claude-opus-4-7", message_id="a")
    b = _make_turn(model="claude-opus-4-7", message_id="b")
    out = _render_to_text([a, b])
    assert "boundary:" not in out


def test_divider_between_different_models():
    a = _make_turn(model="claude-opus-4-6", message_id="a")
    b = _make_turn(model="claude-opus-4-7", message_id="b")
    out = _render_to_text([a, b])
    assert "boundary:" in out
    assert "claude-opus-4-6" in out
    assert "claude-opus-4-7" in out


def test_divider_only_at_transitions():
    # Three turns: 4-6, 4-7, 4-7. One boundary, between turns 1 and 2.
    a = _make_turn(model="claude-opus-4-6", message_id="a")
    b = _make_turn(model="claude-opus-4-7", message_id="b")
    c = _make_turn(model="claude-opus-4-7", message_id="c")
    out = _render_to_text([a, b, c])
    assert out.count("boundary:") == 1


