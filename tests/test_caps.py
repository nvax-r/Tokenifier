"""Tests for tokenifier.caps."""
import pytest

from tokenifier.caps import (
    CAPS,
    DEFAULT_CONTEXT_WINDOW,
    ONE_M_CONTEXT_WINDOW,
    lookup,
)


def test_known_model_returns_caps():
    assert lookup("claude-opus-4-7") == (200_000, 128_000)
    assert lookup("claude-opus-4-6") == (200_000, 128_000)
    assert lookup("claude-sonnet-4-6") == (200_000, 128_000)
    # Haiku has a smaller output cap (64K) — explicit assertion prevents
    # silent regression to the 128K default if the registry is edited.
    assert lookup("claude-haiku-4-5") == (200_000, 64_000)


def test_strips_1m_suffix_and_uses_1m_window():
    # `[1m]` suffix swaps the context window to 1M; output cap unchanged.
    assert lookup("claude-opus-4-7[1m]") == (ONE_M_CONTEXT_WINDOW, 128_000)


def test_unknown_model_warns_and_returns_default():
    with pytest.warns(UserWarning, match="unknown model"):
        ctx, cap = lookup("claude-mystery-9000")
    assert ctx == DEFAULT_CONTEXT_WINDOW
    assert cap == 128_000


def test_unknown_model_with_1m_suffix_still_returns_1m():
    # Defensive: even if the base model is unknown, [1m] should still take the 1M window.
    with pytest.warns(UserWarning):
        ctx, cap = lookup("claude-mystery-9000[1m]")
    assert ctx == ONE_M_CONTEXT_WINDOW
    assert cap == 128_000


def test_caps_registry_contains_documented_models():
    # Smoke test: AGENTS.md lists at least these 4 model families.
    assert "claude-opus-4-7" in CAPS
    assert "claude-opus-4-6" in CAPS
    assert "claude-sonnet-4-6" in CAPS
    assert "claude-haiku-4-5" in CAPS


def test_default_constants():
    assert DEFAULT_CONTEXT_WINDOW == 200_000
    assert ONE_M_CONTEXT_WINDOW == 1_000_000
