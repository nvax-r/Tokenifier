"""Tests for tokenifier.render."""
from datetime import datetime, timezone

import pytest
from rich.console import Console

from tokenifier.model import Talk, Turn, Usage
from tokenifier.render import (
    render,
    BAR_FILL_CHAR,
    BAR_EMPTY_CHAR,
    _bar_width,
    _segmented_bar,
    _overlay_threshold_marker,
)


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


# ---- Segmented input bar ----


def test_segmented_bar_carryover_only():
    bar = _segmented_bar(carryover_ratio=0.5, delta_ratio=0.0, bar_width=40)
    assert "[cyan]" in bar
    assert "[magenta]" not in bar
    assert "[dim]" in bar


def test_segmented_bar_delta_only():
    bar = _segmented_bar(carryover_ratio=0.0, delta_ratio=0.5, bar_width=40)
    assert "[magenta]" in bar
    assert "[cyan]" not in bar
    assert "[dim]" in bar


def test_segmented_bar_both_segments_split_proportionally():
    # 40 cells * 0.6 input_ratio = 24 filled, of which delta_share = 0.2/0.6
    # → 8 magenta, 16 cyan, 16 dim.
    bar = _segmented_bar(carryover_ratio=0.4, delta_ratio=0.2, bar_width=40)
    assert "[cyan]" in bar
    assert "[magenta]" in bar
    assert bar.count(BAR_FILL_CHAR) == 24
    assert bar.count(BAR_EMPTY_CHAR) == 16


def test_segmented_bar_empty_when_both_zero():
    bar = _segmented_bar(carryover_ratio=0.0, delta_ratio=0.0, bar_width=40)
    assert "[cyan]" not in bar
    assert "[magenta]" not in bar
    assert bar.count(BAR_EMPTY_CHAR) == 40


def test_segmented_bar_tiny_delta_clamped_to_at_least_one_cell():
    # carryover_ratio 0.5 (20 cells), delta_ratio 0.01 (0.4 cells → 0).
    # Should be forced to 1 magenta cell so the delta is visible.
    bar = _segmented_bar(carryover_ratio=0.5, delta_ratio=0.01, bar_width=40)
    assert "[magenta]" in bar
    assert "[cyan]" in bar


def test_segmented_bar_tiny_carryover_clamped_to_at_least_one_cell():
    # carryover 0.01 (would round to 0 cells alongside large delta) → forced to 1.
    bar = _segmented_bar(carryover_ratio=0.01, delta_ratio=0.5, bar_width=40)
    assert "[cyan]" in bar
    assert "[magenta]" in bar


def test_segmented_bar_single_filled_cell_with_both_sides_renders_magenta():
    # input_ratio = 0.025 → 1 filled cell. Both sides positive.
    # Per spec: render the single cell as magenta.
    bar = _segmented_bar(carryover_ratio=0.02, delta_ratio=0.005, bar_width=40)
    assert "[magenta]" in bar
    assert "[cyan]" not in bar


# ---- Talk delta annotation ----


def test_first_talk_annotation_shows_full_delta():
    # Single talk, 64K input on 200K → 32%. delta = full 64K (no prior).
    turn = _make_turn(input_total=64_000)
    out = _render_to_text([turn])
    assert "+64K" in out


def test_subsequent_talk_annotation_shows_delta_only():
    # Talk 1: 50K input. Talk 2: 80K input → delta = 30K.
    a = _make_turn(input_total=50_000, message_id="a")
    b = _make_turn(input_total=80_000, message_id="b")
    out = _render_to_text([a, b])
    # Talk 1 says +50K, talk 2 says +30K.
    assert "+50K" in out
    assert "+30K" in out


def test_compact_talk_uses_compact_annotation():
    # Talk 1: 180K input. Talk 2: 40K input (compact event) → annotate "compact".
    a = _make_turn(input_total=180_000, message_id="a")
    b = _make_turn(input_total=40_000, message_id="b")
    out = _render_to_text([a, b])
    assert "compact" in out
    # Make sure we did not emit a phantom positive delta for the compact talk.
    # (Talk 2 should NOT have any "+NK" line — only Talk 1's "+180K" remains.)
    talk2_block = out.split("Talk  2")[1]
    assert "+" not in talk2_block.split("free")[0]


# ---- Threshold marker overlay ----


def test_threshold_marker_in_dim_region():
    # 32% filled, threshold at 92% (cell 36 of 40) → marker lands in dim.
    bar = _segmented_bar(carryover_ratio=0.32, delta_ratio=0.0, bar_width=40)
    out = _overlay_threshold_marker(bar, 0.92, 40)
    assert "[yellow]│[/yellow]" in out
    # Cyan segment is intact (marker did not split it).
    assert out.count("[cyan]") == 1
    # Dim segment was split into two runs.
    assert out.count("[dim]") == 2


def test_threshold_marker_in_filled_region():
    # 95% filled, threshold at 92% (cell 36 of 40) → marker overlays cyan.
    bar = _segmented_bar(carryover_ratio=0.95, delta_ratio=0.0, bar_width=40)
    out = _overlay_threshold_marker(bar, 0.92, 40)
    assert "[yellow]│[/yellow]" in out
    # Cyan segment was split into two runs.
    assert out.count("[cyan]") == 2


def test_threshold_marker_replaces_exactly_one_cell():
    # Total fill+empty chars before overlay = bar_width.
    # After overlay: bar_width - 1 fill+empty chars + 1 yellow │.
    bar = _segmented_bar(carryover_ratio=0.5, delta_ratio=0.0, bar_width=40)
    out = _overlay_threshold_marker(bar, 0.92, 40)
    fill_empty = out.count(BAR_FILL_CHAR) + out.count(BAR_EMPTY_CHAR)
    assert fill_empty == 39
    assert out.count("│") == 1


def test_threshold_marker_position_scales_with_width():
    # At width 20, threshold 0.5 → cell 10. At width 40, → cell 20.
    bar20 = _segmented_bar(carryover_ratio=0.0, delta_ratio=0.0, bar_width=20)
    bar40 = _segmented_bar(carryover_ratio=0.0, delta_ratio=0.0, bar_width=40)
    out20 = _overlay_threshold_marker(bar20, 0.5, 20)
    out40 = _overlay_threshold_marker(bar40, 0.5, 40)
    # Both have a marker; the dim-cell counts before the marker reflect
    # the proportional position.
    # bar20: 10 dim cells, then │, then 9 dim cells.
    # bar40: 20 dim cells, then │, then 19 dim cells.
    assert "[dim]" + BAR_EMPTY_CHAR * 10 + "[/dim][yellow]│[/yellow]" in out20
    assert "[dim]" + BAR_EMPTY_CHAR * 20 + "[/dim][yellow]│[/yellow]" in out40


def test_threshold_marker_at_segment_boundary():
    # carryover_ratio = 0.5, threshold_ratio = 0.5, width 40 → marker at cell 20.
    # Cyan segment runs cells 0..19, dim runs cells 20..39. Marker_cell=20
    # belongs to the dim segment (start <= 20 < end).
    bar = _segmented_bar(carryover_ratio=0.5, delta_ratio=0.0, bar_width=40)
    out = _overlay_threshold_marker(bar, 0.5, 40)
    # Cyan stays intact (one [cyan]…[/cyan] run of 20 cells).
    assert "[cyan]" + BAR_FILL_CHAR * 20 + "[/cyan]" in out
    # Dim is split with the marker between the two halves.
    assert "[/cyan][yellow]│[/yellow][dim]" in out


def test_threshold_marker_zero_bar_width_returns_input():
    out = _overlay_threshold_marker("", 0.92, 0)
    assert out == ""

