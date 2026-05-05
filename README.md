# Tokenifier

> Forensic per-talk context-budget visualiser for Claude Code session transcripts.

You finish a long Claude Code session and wonder: *which* talk caused input to spike? *Where* did Claude come close to the truncation cap? *Why* did context fill up so fast? Claude Code's `/context` shows the current state but no history. Tokenifier reads the session JSONL after the fact and plots every talk so you can see the trajectory.

```
Talk  5 │ claude-opus-4-7[1m] [1M]   12 turns
        │ input  ███░░░░░░░░░░░░░░░░░░░░░░░░   77K  ( 8%)
        │ output ████░░░░░░░░░░░░░░░░░░░░░░░   12K  ( 1% of window · 10% of 128K cap)
        │ free                                911K headroom
```

---

## Why this exists

Context efficiency matters. Two things to optimise:

1. **Input tokens should be useful.** Every token Claude reads — your project docs, prior conversation, tool results, system prompt — competes for finite window space. Wasted input is wasted context.
2. **Output tokens should be maximised.** The output cap (e.g. 128K for Opus) is where Claude *thinks and answers*. Less output room means shorter responses, less thorough reasoning, and more truncation risk.

When input is bloated and output is tight, your context is bad. When input is lean and output has room, your context is good. Tokenifier helps you see which.

This is **not** a cost tracker. [`ccusage`](https://github.com/ryoppippi/ccusage) covers that. Tokenifier measures *space*, not *money*.

---

## What's in your context

Three categories of tokens, each with different behaviour. Tokenifier visualises all three.

### Input tokens

Everything sent INTO Claude before each turn:

- System prompt (Claude Code's internal preamble)
- Project docs (`AGENTS.md`, `CLAUDE.md`, etc. — re-read every turn)
- Your prompts
- All prior assistant responses (re-fed each turn as conversation history)
- Tool results (file contents, grep results, command output)
- Anything you literally pasted

**Cumulative.** Grows across a session. Counted in full every turn — Claude has no memory between turns; the whole history is replayed each time. Anthropic caches it for billing efficiency, but for context-window math the cache split doesn't matter — it all counts equally against the window.

### Output tokens

Everything Claude generates in a single turn:

- Tool calls (JSON args for `Read`, `Bash`, `Edit`, etc.)
- Text replies
- **Extended thinking ("reasoning") tokens** — Claude's internal deliberation before producing a final answer

**Per-turn.** Fresh count each generation. Capped at the model's output cap (e.g. 128K for Opus). After a turn, the output gets folded into history, so it becomes part of the *input* of the next turn.

### Reasoning tokens

A subset of output tokens. Claude "thinks" before answering — that thinking is part of `output_tokens` in the JSONL, **not a separate field**. So when you see "output: 12K" for a talk, that includes thinking + tool calls + final text combined.

Reasoning is double-edged:

- More room for reasoning → better answers
- But reasoning shares the output cap with the final response
- And once it's done, it becomes input on the next turn (carrying forward as history)

---

## What good context looks like

| Bar | Healthy state |
|---|---|
| **Input** | Below ~70% of window most of the session; below 85% always |
| **Output** | Visible bars (Claude is producing real work) but never near the cap |
| **Free** | Substantial — there's room to think |
| **⚠ markers** | None or very rare |

Bad context looks like:

- Input climbing high on simple talks (bloated docs)
- Output approaching the cap (truncation imminent)
- Many turns per talk with tiny output (Claude grinding without progress)
- Sudden input spikes (something big loaded that shouldn't have been)

---

## Install

Pick one.

### Shell alias — recommended for development

Always uses live source. Edit code, alias auto-updates.

```bash
echo "alias tokenifier='uv run --project <path/to/Tokenifier> tokenifier'" >> ~/.zshrc
source ~/.zshrc
```

### `uv tool install` — recommended for stable daily use

Installs as a real binary on your `PATH`.

```bash
uv tool install <path/to/Tokenifier>
```

To update after pulling code changes: `uv tool install --force <path/to/Tokenifier>`.

### Raw `uv run` — no install

Works from anywhere, no setup. Verbose to type.

```bash
uv run --project <path/to/Tokenifier> tokenifier
```

---

## All the ways to use it

### 1. Show the latest session for the current project

```bash
cd ~/your-project
tokenifier
```

Looks up `~/.claude/projects/<encoded-cwd>/` (path with `/` replaced by `-`), picks the most recently modified `.jsonl` there, and renders it.

If no sessions exist for that cwd, you'll see:

```
No JSONL sessions found in /Users/you/.claude/projects/-Users-you-your-project
Try: tokenifier <path/to/session.jsonl>
```

### 2. Show a specific session

```bash
tokenifier ~/.claude/projects/-Users-you-some-project/<uuid>.jsonl
```

For inspecting older sessions, or sessions from a project you're not currently in.

### 3. List your sessions to pick one

Sessions are named by UUID. List them sorted by recency:

```bash
ls -lt ~/.claude/projects/-Users-you-some-project/*.jsonl
```

Pick a UUID and pass the path to `tokenifier`.

### 4. From any directory — target another project

You don't have to `cd`. Just pass an absolute path:

```bash
tokenifier ~/.claude/projects/-Users-you-other-project/<uuid>.jsonl
```

### 5. Save the report to a file

```bash
tokenifier > /tmp/report.md
```

The chart goes to stdout. Metadata (which file was read, talk/turn counts) goes to stderr — so you can redirect them separately.

### 6. Page through a long session

```bash
tokenifier | less -R   # -R preserves the colour codes
```

### 7. Render at a specific terminal width

When piping, rich auto-detects 80 columns. Override:

```bash
COLUMNS=160 tokenifier > /tmp/report.md
```

### 8. Force "latest" explicitly

```bash
tokenifier --latest
```

Same behaviour as `tokenifier` with no args. Use in scripts where intent should be explicit.

### 9. See CLI help

```bash
tokenifier --help
```

---

## How to read the chart

Each block is one **talk** — one user prompt plus every assistant generation until your next prompt.

```
Talk  5 │ claude-opus-4-7[1m] [1M]   12 turns
        │ input  ███░░░░░░░░░░░░░░░░░░░░░░░░   77K  ( 8%)
        │ output ████░░░░░░░░░░░░░░░░░░░░░░░   12K  ( 1% of window · 10% of 128K cap)
        │ free                                911K headroom
```

**Header line** — talk number, model identifier, context window in brackets, turn count. A `⚠` marker appears at the end of the header when input ≥ 85% of the window.

**Input bar** (cyan) — cumulative input at the END of the talk. Includes everything from the start of the session up through this talk's last turn.

**Output bar** (yellow) — total output Claude produced across all turns in this talk. Scaled to the output cap (the truncation lever): if this bar fills, Claude was about to be cut off.

**Free bar** (dim) — what's left in the window after this talk.

**Boundary divider** — appears between talks if the model changed (e.g. Opus 4.6 → 4.7) — tokenizer drift means percentages aren't directly comparable across the boundary.

### Hierarchy

```
Session  = one .jsonl file = one Claude Code conversation
  ↓
Talk     = one user prompt → all assistant work until next user prompt
  ↓
Turn     = one assistant API call (one model generation)
```

A talk-heavy with tool use can contain 20+ turns. A simple "yes" exchange is one talk with one turn.

---

## How to evaluate your prompts and documents

Read the chart and look for these patterns. Each is a signal you can act on.

### Signal: input bar is high even on simple talks

Your project docs are bloated, your system prompt is heavy, or your shell hooks are loading a lot. Every talk pays the input cost.

**Fix:** trim `AGENTS.md` / `CLAUDE.md`. Anything that isn't actively shaping Claude's behaviour is dead weight. Move detailed reference material into separate files Claude can read on demand instead of loading every turn.

### Signal: sudden input spike on one talk

One talk grew much more than the prior. Likely cause: you pasted a big chunk of code, or a tool returned a huge result, or Claude read a giant file.

**Fix:** check that talk. Was the input useful? If you pasted code, could you have let Claude `Read` the file selectively? If a tool returned huge content, can you `grep` before reading?

### Signal: many turns per talk + tiny output

Claude is grinding through tool calls without synthesising. Often means:

- The task was too vague (no clear endpoint)
- Tool results aren't helping (lots of greps returning nothing)
- Claude is exploring instead of producing

**Fix:** break the task down. First "explore the codebase and summarise"; then "now do X". Per-talk has a clearer success condition.

### Signal: output bar approaches the cap

Claude was about to be truncated mid-response. The prompt asked for too much in one turn, or context is so full Claude has no room for a thorough answer.

**Fix:** reset the session and retry with a tighter scope.

### Signal: input drops sharply between talks

Auto-compaction kicked in. Claude Code summarised earlier history into shorter form. You've lost some fidelity in prior conversation; tasks that depend on details from before the compact may be confused.

**Fix:** if compaction happened, consider whether to start a fresh session for the next phase of work. Compaction is lossy.

### Signal: input climbs steadily even on metadata talks

Trivial talks like "yes" or "ok" still grow input by a few thousand tokens. That's the compounding cost of conversation history.

**Fix:** for very long projects, plan your sessions. Do related work in one session; switch sessions for unrelated work. Don't let every conversation become the one that goes for 6 hours.

---

## What it deliberately doesn't do

- **Dollar-cost calculation** — use [ccusage](https://github.com/ryoppippi/ccusage)
- **Real-time tailing** — runs against the JSONL after the fact
- **Prompt-content analysis** — only structural sizes; we don't read your prose
- **Sub-agent traffic** — sub-agents (sidechains) live in `<session-id>/subagents/agent-*.jsonl` and aren't included in the main view
- **Codex CLI logs** — different schema; out of scope

---

## Two things Tokenifier handles automatically

**Dedupe by `message.id`.** Parallel tool calls within one turn share an ID in the JSONL — schema reconnaissance found groups of up to 13 rows for one logical generation. Naively summing would 13× the count. Tokenifier dedupes so each generation is counted once.

**1M Opus auto-detection.** When you opt into 1M Opus via `ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-7[1m]'`, the JSONL still stores plain `claude-opus-4-7` (no suffix). Tokenifier observes input exceeding the documented 200K window and infers the variant — the header shows `[1M]` and percentages are honest. No flag needed.

---

## Project layout

```
tokenifier/
├── model.py     # Pydantic Usage / Turn / Talk
├── caps.py      # Model → (context window, output cap) registry
├── parser.py    # JSONL → list[Talk] with filter + dedupe + 1M detect
├── render.py    # list[Talk] → terminal output via rich
├── cli.py       # typer entrypoint
└── __init__.py
tests/                       # 38 unit tests
scripts/recon.py             # schema reconnaissance script (one-shot, preserved)
docs/superpowers/specs/      # design docs (gitignored)
```

For architecture rationale and the full gotchas list, see [`AGENTS.md`](./AGENTS.md).

## Tests

```bash
cd <path/to/Tokenifier>
uv run pytest -v
```

38 tests cover the data model, caps registry with `[1m]` handling, JSONL parser filter predicates, talk-grouping logic, 1M auto-promotion, and renderer behaviour (bar widths, danger marker, boundary divider).
