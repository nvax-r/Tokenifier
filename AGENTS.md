# AGENTS.md

## Project: Tokenifier

A small Python CLI that turns Claude Code session transcripts into per-turn **context budget** plots. For each prompt/action, it shows whether we sent too much input, whether output is approaching its cap, and how much of the window is left.

This is **not** a cost tracker. `ccusage` already does that. We measure *space*, not *money*.

## Why this exists

Claude Code's `/context` shows the *current* fill level but not history. There's no easy way to see "did the turn 3 prompts ago suddenly eat 80K?" without grepping JSONL by hand. Tokenifier answers that with a per-turn timeline.

## The visualization

For each assistant turn, render one row:

```
Turn 12 │ opus-4-7 [200K]
        │ input  ████████░░░░░░░░░░░░░░░░░░░░░░  46K  (23%)
        │ output ██░                              8K   (4% of window · 6% of 128K cap)
        │ free                                  146K headroom
```

Three numbers matter per turn:
- **input %** of context window — the warning lever ("am I sending too much?")
- **output %** of context window
- **output %** of model's output cap — the truncation lever ("did Claude run out of room?")

Across a session, also render a compact summary table: turn # / model / input % / output % / output-vs-cap %.

## Architecture

```
tokenifier/
├── tokenifier/
│   ├── __init__.py
│   ├── parser.py     # JSONL → list[Turn]; pure, no I/O side effects beyond reading the file
│   ├── model.py      # pydantic schemas + Turn dataclass
│   ├── caps.py       # model-name → (context_window, output_cap) registry
│   ├── render.py     # rich-based rendering of list[Turn]
│   └── cli.py        # typer entrypoint
├── tests/
│   └── fixtures/     # anonymized JSONL samples
├── CLAUDE.md
├── AGENTS.md
└── pyproject.toml
```

The parser is the heart. Keep it pure: path in, `list[Turn]` out. No printing, no global state. This makes it deterministically testable.

## Data model

```python
class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

class Turn(BaseModel):
    message_id: str
    timestamp: datetime  # timezone-aware
    model: str           # e.g. "claude-opus-4-7"
    usage: Usage
    is_sidechain: bool = False
    is_error: bool = False

    @property
    def input_total(self) -> int:
        return (self.usage.input_tokens
              + self.usage.cache_read_input_tokens
              + self.usage.cache_creation_input_tokens)
```

## Model caps registry (`caps.py`)

| Model id            | Default ctx | 1M variant     | Output cap (sync) |
|---------------------|-------------|----------------|-------------------|
| claude-opus-4-7     | 200K        | `[1m]` suffix  | 128K              |
| claude-opus-4-6     | 200K        | yes            | (verify)          |
| claude-sonnet-4-6   | 200K        | yes            | (verify)          |
| claude-haiku-4-5    | 200K        | no             | (verify)          |

The 1M Opus variant is opted into by env var: `ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-7[1m]'`. The model id in the JSONL will reflect this — parse the `[1m]` suffix to pick the right context window.

Treat this table as a starting point. Anthropic ships new models. Make the registry easy to extend, and have `render.py` warn (don't crash) on unknown models — default to a 200K assumption with a "ctx unknown" badge.

## Gotchas we will keep getting bitten by

1. **Parallel tool calls share `message.id`.** Dedupe before summing or token totals are inflated. Use a `set[str]` of seen IDs in any aggregation pass.

2. **`output_tokens` already includes extended-thinking tokens.** There is no separate `reasoning_tokens` field in Claude Code's JSONL. Don't fabricate one. If you need to plot reasoning separately, that's a feature request — not something to derive.

3. **Sidechains live in their own context window.** A subagent's `input_tokens` is *not* part of the parent turn's input. Filter `isSidechain == true` for context-occupancy math; you may want a separate "subagent activity" view that includes them.

4. **`input_tokens` is cumulative-by-design.** Each turn's input includes prior conversation. To see "how much *new* input did this turn add," diff with the previous turn's input total. This is the right lens for spotting a rogue file load.

5. **Tokenizer changes between major model versions.** Opus 4.7 uses ~1.0–1.35× the tokens 4.6 did for the same text. A line chart of "input over time" that crosses a model boundary will show a fake jump. Either segment the chart by model, or annotate the boundary.

6. **Auto-compact rewrites history.** When Claude Code compacts, the next turn's `input_tokens` drops sharply. The drop is real and the chart should show it cleanly — don't smooth it away.

## Conventions

- All token math returns `int`. No floats.
- All times are timezone-aware. JSONL stores ISO timestamps with offset; preserve it.
- No silent fallbacks. Schema-non-conformant rows are logged and skipped, never fabricated.
- Tests use fixtures in `tests/fixtures/`, never `~/.claude/`. The user's real session data is off-limits to tests.
- Renderer warns and proceeds on unknown models; the parser raises on truly malformed data.

## Out of scope (and the reasons)

- **Codex CLI rollouts** — different schema. Codex stores cumulative totals per event (must be diffed), exposes `reasoning_output_tokens` separately, and groups by `turn_context.model`. The parser asymmetry is significant; don't unify prematurely.
- **Real-time tailing** — needs a file watcher and a redrawing TUI. v2 material.
- **Dollar-cost calculation** — `ccusage` covers this well.
- **Web dashboard** — terminal first.

If a feature pulls toward any of these, stop, note it in `DECISIONS.md`, and confirm before expanding scope.
