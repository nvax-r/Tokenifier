"""Terminal renderer for Tokenifier — per-talk layout-A.

Renders a list of `Talk` objects (each = one fresh user prompt + every
assistant turn that follows it until the next user prompt) as per-talk
4-line detail blocks.

Layout (one talk):

    Talk  5 │ opus-4-7 [200K]   12 turns
            │ input  ██████████░░░░░░░░░░░░░░░░░░░░   64K  (32%)
            │ output █░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    8K  ( 4% of window · 6% of 128K cap)
            │ free                                    128K headroom

`input` shows the cumulative input at the END of the talk (high-water mark).
`output` shows the SUM of output_tokens across every turn in the talk.

When the model changes between talks, a one-line divider is printed
between them (per AGENTS.md gotcha #5 — tokenizer drift across model
versions).
"""
from __future__ import annotations

import re

from rich.console import Console
from rich.markup import escape

from tokenifier.caps import lookup
from tokenifier.model import Talk


BAR_FILL_CHAR = "█"
BAR_EMPTY_CHAR = "░"
COMPACT_THRESHOLD = 0.92


def _bar_width(terminal_width: int) -> int:
    """Number of bar cells, scaled to terminal width."""
    return min(40, max(20, terminal_width - 40))


def _format_tokens(n: int) -> str:
    """Render a token count compactly: 64_000 → '64K', 1_000_000 → '1M'."""
    if n >= 1_000_000:
        return f"{n // 1_000_000}M"
    if n >= 1000:
        return f"{n // 1000}K"
    return str(n)


def _bar(ratio: float, bar_width: int, color: str) -> str:
    """Render one bar of `bar_width` cells, `ratio` filled in `color`.

    Any nonzero ratio renders at least 1 fill char so small-but-present
    values are still visible (otherwise small outputs vs huge windows
    look identical to no output at all). Truly zero ratios stay empty.
    """
    if ratio > 0:
        fill = max(1, int(ratio * bar_width))
    else:
        fill = 0
    empty = bar_width - fill
    fill_part = f"[{color}]{BAR_FILL_CHAR * fill}[/{color}]"
    empty_part = f"[dim]{BAR_EMPTY_CHAR * empty}[/dim]"
    return fill_part + empty_part


def _segmented_bar(
    carryover_ratio: float,
    delta_ratio: float,
    bar_width: int,
) -> str:
    """Bar with two adjacent colored segments: cyan carryover, magenta delta.

    See plan 2026-05-06 for cell-allocation rules.
    """
    input_ratio = carryover_ratio + delta_ratio
    if input_ratio <= 0:
        return f"[dim]{BAR_EMPTY_CHAR * bar_width}[/dim]"

    filled_cells = max(1, int(input_ratio * bar_width))
    if filled_cells > bar_width:
        filled_cells = bar_width

    if delta_ratio <= 0:
        delta_cells = 0
    elif carryover_ratio <= 0:
        delta_cells = filled_cells
    elif filled_cells == 1:
        # Both positive but only one cell — show the delta (it's the
        # actionable signal; the carryover is implied by prior bars).
        delta_cells = 1
    else:
        delta_share = delta_ratio / input_ratio
        delta_cells = int(delta_share * filled_cells)
        if delta_cells == 0:
            delta_cells = 1
        elif delta_cells == filled_cells:
            delta_cells = filled_cells - 1

    carryover_cells = filled_cells - delta_cells
    empty_cells = bar_width - filled_cells

    parts = []
    if carryover_cells > 0:
        parts.append(f"[cyan]{BAR_FILL_CHAR * carryover_cells}[/cyan]")
    if delta_cells > 0:
        parts.append(f"[magenta]{BAR_FILL_CHAR * delta_cells}[/magenta]")
    if empty_cells > 0:
        parts.append(f"[dim]{BAR_EMPTY_CHAR * empty_cells}[/dim]")
    return "".join(parts)


_SEG_RE = re.compile(r"\[(\w+)\]([^\[]+)\[/\w+\]")


def _overlay_threshold_marker(
    bar: str,
    threshold_ratio: float,
    bar_width: int,
) -> str:
    """Replace one cell of `bar` with [yellow]│[/yellow] at the threshold index.

    `bar` is the rich-markup string from `_segmented_bar`:
        [cyan]████[/cyan][magenta]██[/magenta][dim]░░░░░░░░[/dim]
    The function locates the segment containing cell
    `int(threshold_ratio * bar_width)` and splits it around a 1-cell
    yellow `│`. Returns `bar` unchanged when `bar_width <= 0`.
    """
    if bar_width <= 0:
        return bar

    marker_cell = max(0, min(bar_width - 1, int(threshold_ratio * bar_width)))

    out_parts: list[str] = []
    cell_offset = 0
    last_end = 0
    for match in _SEG_RE.finditer(bar):
        if match.start() > last_end:
            out_parts.append(bar[last_end:match.start()])

        tag = match.group(1)
        content = match.group(2)
        seg_len = len(content)
        seg_start = cell_offset
        seg_end = cell_offset + seg_len

        if seg_start <= marker_cell < seg_end:
            local_idx = marker_cell - seg_start
            before = content[:local_idx]
            after = content[local_idx + 1 :]
            if before:
                out_parts.append(f"[{tag}]{before}[/{tag}]")
            out_parts.append("[yellow]│[/yellow]")
            if after:
                out_parts.append(f"[{tag}]{after}[/{tag}]")
        else:
            out_parts.append(match.group(0))

        cell_offset = seg_end
        last_end = match.end()

    if last_end < len(bar):
        out_parts.append(bar[last_end:])

    return "".join(out_parts)


def _maybe_render_boundary(console: Console, prev: Talk | None, curr: Talk) -> None:
    """Print a divider when `curr` switches model from `prev`.

    Per AGENTS.md gotcha #5: tokenizer changes between major model
    versions, so percentages are not directly comparable across the
    boundary.
    """
    if prev is None or prev.model == curr.model:
        return
    console.print(
        f"[dim]── boundary: {escape(prev.model)} → {escape(curr.model)} "
        f"(tokenizer changes; percentages not directly comparable) ──[/dim]"
    )
    console.print()


def _render_talk(
    console: Console,
    talk: Talk,
    idx: int,
    prev_input_total: int,
) -> None:
    """Render one talk's 4-line detail block.

    `prev_input_total` is the previous rendered talk's `final_input_total`
    (or 0 for the first talk). Used to compute and color the input delta.
    """
    bar_width = _bar_width(console.width)
    ctx, cap = lookup(talk.model)

    input_total = talk.final_input_total
    output_total = talk.total_output
    free = max(0, ctx - input_total - output_total)

    raw_delta = input_total - prev_input_total
    is_compact = raw_delta < 0
    delta = max(0, raw_delta)
    carryover = input_total - delta

    input_ratio = input_total / ctx if ctx > 0 else 0.0
    carryover_ratio = carryover / ctx if ctx > 0 else 0.0
    delta_ratio = delta / ctx if ctx > 0 else 0.0
    output_ratio_window = output_total / ctx if ctx > 0 else 0.0
    output_ratio_cap = output_total / cap if cap > 0 else 0.0

    danger = input_ratio >= COMPACT_THRESHOLD
    danger_marker = "  [red]⚠[/red]" if danger else ""

    # Header: "Talk N │ model [WINDOW]   K turn(s)  ⚠"
    ctx_label = _format_tokens(ctx)
    turn_label = "turn" if talk.turn_count == 1 else "turns"
    console.print(
        f"Talk {idx:>2} │ {escape(talk.model)} \\[{ctx_label}]   "
        f"{talk.turn_count} {turn_label}{danger_marker}"
    )

    # Input bar — segmented carryover + delta, then overlay the
    # auto-compact threshold marker at the configured ratio.
    input_bar = _segmented_bar(carryover_ratio, delta_ratio, bar_width)
    input_bar = _overlay_threshold_marker(input_bar, COMPACT_THRESHOLD, bar_width)
    input_value = _format_tokens(input_total).rjust(5)
    pct = int(round(input_ratio * 100))
    if is_compact:
        input_annotation = f"({pct:>2}% · compact)"
    else:
        input_annotation = f"({pct:>2}% · +{_format_tokens(delta)})"
    console.print(f"        │ input  {input_bar}  {input_value}  {input_annotation}")

    # Output bar — scaled to cap (truncation lever per AGENTS.md).
    output_bar = _bar(output_ratio_cap, bar_width, "yellow")
    output_value = _format_tokens(output_total).rjust(5)
    output_pct_window = int(round(output_ratio_window * 100))
    output_pct_cap = int(round(output_ratio_cap * 100))
    cap_label = _format_tokens(cap)
    console.print(
        f"        │ output {output_bar}  {output_value}  "
        f"({output_pct_window:>2}% of window · {output_pct_cap}% of {cap_label} cap)"
    )

    # Free / headroom.
    free_value = _format_tokens(free).rjust(5)
    pad = " " * bar_width
    console.print(f"        │ free   [dim]{pad}[/dim]   {free_value} headroom")
    console.print()  # blank line between talks


def render(talks: list[Talk], console: Console | None = None) -> None:
    """Render `talks` as per-talk detail blocks to the terminal.

    Pass an explicit `Console(width=W, record=True)` in tests to capture
    output deterministically.
    """
    if console is None:
        console = Console()
    prev: Talk | None = None
    prev_input_total: int = 0
    for idx, talk in enumerate(talks, start=1):
        if not talk.turns:
            continue
        _maybe_render_boundary(console, prev, talk)
        _render_talk(console, talk, idx, prev_input_total)
        prev = talk
        prev_input_total = talk.final_input_total
