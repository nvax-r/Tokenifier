"""Pydantic data model for Tokenifier.

Defines `Usage` (token counts per turn) and `Turn` (one assistant turn,
post-parse). The schema is parser-ready: Phase 2 will populate these
instances from JSONL rows. Phase 1 hand-crafts a sample list and feeds
it to the renderer.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Usage(BaseModel):
    """Token counts on one assistant turn.

    The four documented fields (per AGENTS.md) are required-with-default-0.
    Pydantic's `extra="allow"` accepts the six undocumented sub-keys the
    recon note flagged (`service_tier`, `cache_creation` (nested dict),
    `inference_geo`, `server_tool_use`, `iterations`, `speed`) without
    crashing — Phase 1 doesn't surface them.
    """
    model_config = ConfigDict(extra="allow")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class Turn(BaseModel):
    """One assistant turn from a Claude Code session, post-parse."""
    message_id: str
    timestamp: datetime
    model: str
    usage: Usage
    is_sidechain: bool = False
    is_error: bool = False

    @property
    def input_total(self) -> int:
        """Total input tokens occupying the context window this turn.

        Sum of fresh + cache_read + cache_creation. All three count
        equally against the window per AGENTS.md non-negotiable rule #3.
        """
        return (
            self.usage.input_tokens
            + self.usage.cache_read_input_tokens
            + self.usage.cache_creation_input_tokens
        )


class Talk(BaseModel):
    """One conversational exchange: a fresh user prompt plus every assistant
    turn that follows it until the next fresh user prompt.

    Tool-result rows are NOT talk boundaries — they're mid-talk
    Claude→tool→Claude plumbing. A single talk can contain many tool
    roundtrips and therefore many assistant turns.
    """
    turns: list[Turn]

    @property
    def model(self) -> str:
        """Representative model — the one used by the last turn of the talk."""
        return self.turns[-1].model

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def final_input_total(self) -> int:
        """Cumulative input occupancy at the END of the talk.

        Each turn's `input_total` already includes everything before it,
        so the last turn's value is the talk's high-water mark.
        """
        return self.turns[-1].input_total

    @property
    def total_output(self) -> int:
        """Sum of `output_tokens` across every assistant turn in this talk."""
        return sum(t.usage.output_tokens for t in self.turns)
