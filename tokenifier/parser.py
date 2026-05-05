"""JSONL parser for Tokenifier.

Reads Claude Code session JSONL transcripts and produces a list of `Talk`
objects, where each Talk groups consecutive assistant turns that share a
single user-prompt boundary.

Filter predicates (per recon note 2026-05-05-jsonl-schema-recon.md):

1. `row["type"] == "assistant"` — non-assistant rows have no token usage.
2. `not row.get("isApiErrorMessage")` — synthetic error stubs have all-zero usage.
3. `row["message"]["model"] != "<synthetic>"` — defensive duplicate of (2).
4. Dedupe by `row["message"]["id"]` — parallel tool calls in one turn share an id.

Talk boundary: a `type == "user"` row whose `message.content` is either a
plain string OR a list whose first item is `type: "text"`. Tool-result
user rows (`content[0].type == "tool_result"`) are mid-talk and don't
start a new Talk.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from tokenifier.caps import lookup
from tokenifier.model import Talk, Turn, Usage


def read_jsonl(path: Path) -> Iterator[dict]:
    """Yield each valid JSON object from a JSONL file.

    Empty lines are skipped silently. Malformed lines log to stderr and
    are skipped.

    Raises `FileNotFoundError` if the file does not exist (intentional —
    a typo in a CLI argument should fail loudly, not produce an empty
    report).
    """
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[skip] {path}:{lineno} malformed JSON: {e}", file=sys.stderr)


def _is_user_prompt(row: dict) -> bool:
    """True if `row` is a fresh user prompt (NOT a tool_result row)."""
    if row.get("type") != "user":
        return False
    msg = row.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            return first.get("type") == "text"
    return False


def _row_to_turn(row: dict, seen_ids: set[str]) -> Turn | None:
    """Convert one assistant row to a Turn, or return None if it should be filtered.

    Mutates `seen_ids` to record the message.id when a Turn is produced.
    """
    if row.get("type") != "assistant":
        return None
    if row.get("isApiErrorMessage"):
        return None

    msg = row.get("message")
    if not isinstance(msg, dict):
        return None

    msg_id = msg.get("id")
    if not isinstance(msg_id, str) or msg_id in seen_ids:
        return None

    model = msg.get("model")
    if not isinstance(model, str) or model == "<synthetic>":
        return None

    usage_dict = msg.get("usage")
    if not isinstance(usage_dict, dict):
        return None

    timestamp_str = row.get("timestamp")
    if not isinstance(timestamp_str, str):
        return None

    try:
        turn = Turn(
            message_id=msg_id,
            timestamp=timestamp_str,
            model=model,
            usage=Usage(**usage_dict),
            is_sidechain=bool(row.get("isSidechain", False)),
            is_error=False,
        )
    except ValidationError:
        return None

    seen_ids.add(msg_id)
    return turn


def parse_session(path: Path) -> list[Talk]:
    """Parse a Claude Code session JSONL into a list of Talks.

    Each fresh user prompt opens a new Talk; subsequent assistant turns
    accrete into it until the next fresh user prompt. Empty Talks (those
    with zero assistant turns) are dropped from the result.
    """
    talks: list[Talk] = []
    current_turns: list[Turn] = []
    seen_ids: set[str] = set()

    def flush_current() -> None:
        if current_turns:
            talks.append(Talk(turns=list(current_turns)))

    for row in read_jsonl(path):
        if _is_user_prompt(row):
            flush_current()
            current_turns.clear()
            continue

        turn = _row_to_turn(row, seen_ids)
        if turn is not None:
            current_turns.append(turn)

    flush_current()

    _promote_to_1m_if_needed(talks)
    return talks


def _promote_to_1m_if_needed(talks: list[Talk]) -> None:
    """Auto-detect the 1M Opus variant from observed token counts.

    The recon note flagged that JSONL `message.model` does NOT carry the
    `[1m]` suffix even when the user has opted into the 1M context. So
    real sessions on 1M Opus appear here as plain `claude-opus-4-7` with
    `input_total` exceeding the documented 200K window.

    For each base model where any turn's `input_total` exceeds the
    documented context, promote ALL turns of that model in-place by
    appending `[1m]`, so `caps.lookup` returns the 1M window downstream.
    """
    needs_1m: set[str] = set()
    for talk in talks:
        for turn in talk.turns:
            if turn.model.endswith("[1m]"):
                continue
            ctx, _ = lookup(turn.model)
            if turn.input_total > ctx:
                needs_1m.add(turn.model)

    if not needs_1m:
        return

    for talk in talks:
        new_turns = []
        for turn in talk.turns:
            if turn.model in needs_1m:
                new_turns.append(turn.model_copy(update={"model": turn.model + "[1m]"}))
            else:
                new_turns.append(turn)
        talk.turns = new_turns
