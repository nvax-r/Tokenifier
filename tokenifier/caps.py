"""Model-to-caps registry for Tokenifier.

Maps a model identifier (as it appears in `message.model` of Claude Code
JSONL rows) to a `(context_window, output_cap)` tuple. Recognises the
optional `[1m]` suffix, which substitutes a 1M context window while
keeping the output cap unchanged.
"""
from __future__ import annotations

import warnings


DEFAULT_CONTEXT_WINDOW: int = 200_000
ONE_M_CONTEXT_WINDOW: int = 1_000_000


# (context_window, output_cap) per model id.
# Per AGENTS.md and the recon note (4 confirmed models in use).
CAPS: dict[str, tuple[int, int]] = {
    "claude-opus-4-7":   (200_000, 128_000),
    "claude-opus-4-6":   (200_000, 128_000),
    "claude-sonnet-4-6": (200_000, 128_000),
    "claude-haiku-4-5":  (200_000,  64_000),
}


def lookup(model: str) -> tuple[int, int]:
    """Return (context_window, output_cap) for `model`.

    Recognises an optional `[1m]` suffix: strips it and uses
    ONE_M_CONTEXT_WINDOW instead of the model's default. Output cap is
    unaffected by the suffix.

    Unknown base models warn (UserWarning) and return defaults — the
    renderer continues with a "ctx unknown" badge rather than crashing.
    """
    is_1m = model.endswith("[1m]")
    base = model.removesuffix("[1m]")

    if base in CAPS:
        ctx, cap = CAPS[base]
        if is_1m:
            ctx = ONE_M_CONTEXT_WINDOW
        return ctx, cap

    warnings.warn(f"unknown model {model!r}; using default caps", stacklevel=2)
    ctx = ONE_M_CONTEXT_WINDOW if is_1m else DEFAULT_CONTEXT_WINDOW
    return ctx, 128_000
