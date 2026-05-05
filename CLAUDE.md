# CLAUDE.md

## What this is
**Tokenifier** is a Python terminal tool that reads Claude Code session transcripts and visualizes per-turn context budget usage: how much of the window each turn's input occupied, how much the output used, and how close output got to the model's output cap.

The goal is forensic: spot turns where we sent too much input or where output got truncated.

For architecture, rationale, and the full gotchas list, see `AGENTS.md`.

## Data source
`~/.claude/projects/<url-encoded-project-path>/<session-id>.jsonl`

One line per event. Token data lives on assistant turns under `message.usage`:
- `input_tokens` — fresh (uncached) input
- `cache_creation_input_tokens` — input written to cache this turn
- `cache_read_input_tokens` — input served from cache
- `output_tokens` — generated tokens, **including extended-thinking tokens** (Claude does not expose thinking as a separate field)

## Non-negotiable rules when parsing

1. **Dedupe by `message.id`.** Parallel tool calls in one turn share an ID. Summing without deduping double-counts.
2. **Skip non-main-chain entries** for context-occupancy math:
   - `isSidechain == true` (subagent traffic — lives in its own window)
   - `isApiErrorMessage == true` (failed turns)
3. **Input occupancy = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.** All three count equally against the context window. The cache split is a billing distinction, not a space distinction.
4. **For "current context fullness," take the most recent valid main-chain entry — do not sum across the file.** Each turn's `input_tokens` already includes the full prior conversation.
5. **Never aggregate blindly across model versions.** Opus 4.7 uses a different tokenizer than 4.6 (~1.0–1.35× tokens). Group and label by model.

## Stack
- Python 3.12, managed with `uv`
- `pydantic` for the JSONL row schema
- `rich` for terminal rendering (stacked bars, tables)
- `typer` for the CLI
- `pytest` for tests

## Run / test
- `uv run tokenifier --latest` — render the most recent session
- `uv run tokenifier <session-file>` — render a specific session
- `uv run pytest` — run tests against fixtures

## Out of scope
Codex CLI logs · real-time tailing · web dashboard · dollar-cost calculation.
If a request pulls toward any of these, stop and confirm before expanding scope.
