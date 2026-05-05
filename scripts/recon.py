"""recon.py — JSONL schema reconnaissance for Claude Code session transcripts.

Throwaway script. Stdlib-only. Reads N JSONL files, emits a markdown report of
structural statistics. Used once to verify AGENTS.md's schema claims against
real session data before any production parser code is written.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterator


PROSE_FIELDS = frozenset({
    "content",
    "text",
    "input",
    "output",
    "result",
    "summary",
    "tool_use_input",
    "tool_use_output",
    # Added after Task 7 dry-run on real session JSONL revealed leaks:
    "aiTitle",
    "stdout",
    "stderr",
    "command",
    "lastPrompt",
})


def strip_prose(obj, _under_prose=False):
    """Recursively scrub primitive values under prose-bearing fields.

    Walks dicts and lists. Any primitive (str/int/float/bool/None) encountered
    while under a prose-named ancestor (a key in PROSE_FIELDS) is replaced with
    a placeholder of the form '<stripped:type>'. Structural shape (dict/list
    nesting) is preserved so the recon report can describe schema layout.

    `_under_prose` is the recursion flag — do not pass it from external callers.
    The function returns a new structure; it does not mutate input.
    """
    if isinstance(obj, dict):
        return {
            k: strip_prose(v, _under_prose=_under_prose or k in PROSE_FIELDS)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [strip_prose(item, _under_prose=_under_prose) for item in obj]
    if _under_prose:
        return f"<stripped:{type(obj).__name__}>"
    return obj


def read_jsonl(path: Path) -> Iterator[dict]:
    """Yield each valid JSON object from a JSONL file.

    Empty lines are skipped silently. Malformed lines are skipped with a
    one-line message to stderr (path:lineno). Caller-supplied path can be
    a `Path` or anything `open()` accepts.

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


class Aggregator:
    """Accumulate structural statistics over ingested JSONL rows.

    The aggregator is fed one row at a time via `ingest(row)`. It maintains
    counters keyed by top-level field names, `message.usage` field names,
    `message.model` strings, and top-level `type` markers. It also tracks
    rows grouped by `message.id` so duplicates (parallel tool calls in one
    turn) can be surfaced.

    For each "interesting" category (`error`, `sidechain`, or the row's
    `type` value), `samples` holds one redacted specimen — the first row of
    that category seen, with all prose-bearing fields scrubbed via
    `strip_prose`. This is what the report shows under "Redacted samples."

    Public counters expose Counter objects so callers can use `most_common()`,
    iterate, or read counts directly.
    """

    def __init__(self):
        self.row_count = 0
        self.top_level_keys: Counter = Counter()
        self.usage_keys_present: Counter = Counter()
        self.usage_keys_zero: Counter = Counter()
        self.models: Counter = Counter()
        self.type_markers: Counter = Counter()
        # Holds full row refs (not just ids) so duplicate-group reporting can include type markers.
        # Acceptable for single-run recon over a handful of files; not designed for streaming use.
        self._message_id_to_rows: dict[str, list[dict]] = defaultdict(list)
        self.samples: dict[str, dict] = {}

    def ingest(self, row: dict) -> None:
        """Update all counters, the duplicate-id index, and per-category samples.

        `row` is a dict parsed from one JSONL line. Top-level keys are counted;
        if `row["message"]` is itself a dict, then `model`, `id`, and `usage`
        sub-fields are also counted (each guarded against unexpected types).
        Rows missing these fields, or with non-dict `message`, are silently
        skipped at the relevant level — `ingest` never raises on shape.

        After counting, the row is categorized via `_categorize`. The first
        row of each category seen is stored (prose-stripped) in `self.samples`;
        subsequent rows in the same category are not overwritten.
        """
        self.row_count += 1
        for k in row:
            self.top_level_keys[k] += 1

        msg = row.get("message")
        if isinstance(msg, dict):
            model = msg.get("model")
            if isinstance(model, str):
                self.models[model] += 1

            mid = msg.get("id")
            if isinstance(mid, str):
                self._message_id_to_rows[mid].append(row)

            usage = msg.get("usage")
            if isinstance(usage, dict):
                for k, v in usage.items():
                    self.usage_keys_present[k] += 1
                    # Claude's usage values are always int; `v == 0` would also
                    # match `False`, but that does not occur in real JSONLs.
                    if v == 0:
                        self.usage_keys_zero[k] += 1

        type_marker = row.get("type")
        if isinstance(type_marker, str):
            self.type_markers[type_marker] += 1

        category = self._categorize(row)
        if category and category not in self.samples:
            # `strip_prose` is mandatory here — it is the privacy boundary.
            # See test_samples_have_prose_stripped. Never store `row` directly.
            self.samples[category] = strip_prose(row)

    @staticmethod
    def _categorize(row: dict) -> str | None:
        """Pick the single category label for a row.

        Order matters: error and sidechain are checked before the bare type
        because both flags ride on top of an underlying type — an error row
        is also typically `type == "assistant"`, but we want it filed under
        "error" rather than "assistant".
        """
        if row.get("isApiErrorMessage"):
            return "error"
        if row.get("isSidechain"):
            return "sidechain"
        t = row.get("type")
        if isinstance(t, str):
            return t
        return None

    def duplicate_message_id_groups(self, limit: int = 3) -> list[dict]:
        """Return groups of rows that share a message.id, up to `limit` groups.

        Each entry: {"message_id": str, "count": int, "type_markers": list[str]}.
        Groups are sorted by count descending so the most-shared IDs come first.
        """
        groups = []
        for mid, rows in self._message_id_to_rows.items():
            if len(rows) > 1:
                groups.append({
                    "message_id": mid,
                    "count": len(rows),
                    "type_markers": [r.get("type", "<no-type>") for r in rows],
                })
        groups.sort(key=lambda g: g["count"], reverse=True)
        return groups[:limit]


def format_report(agg: Aggregator) -> str:
    """Render the aggregator state as a markdown report."""
    lines: list[str] = []
    lines.append("# JSONL Schema Reconnaissance — Raw Output")
    lines.append("")
    lines.append(f"Rows ingested: **{agg.row_count}**")
    lines.append("")

    lines.append("## Top-level row keys")
    lines.append("")
    lines.append("| key | count |")
    lines.append("|---|---|")
    for key, count in agg.top_level_keys.most_common():
        lines.append(f"| `{key}` | {count} |")
    lines.append("")

    lines.append("## `message.usage` keys")
    lines.append("")
    lines.append("| key | present | zero |")
    lines.append("|---|---|---|")
    for key, count in agg.usage_keys_present.most_common():
        zero = agg.usage_keys_zero.get(key, 0)
        lines.append(f"| `{key}` | {count} | {zero} |")
    lines.append("")

    lines.append("## Distinct `model` strings")
    lines.append("")
    lines.append("| model | count |")
    lines.append("|---|---|")
    for model, count in agg.models.most_common():
        lines.append(f"| `{model}` | {count} |")
    lines.append("")

    lines.append("## Distinct `type` markers")
    lines.append("")
    lines.append("| type | count |")
    lines.append("|---|---|")
    for type_marker, count in agg.type_markers.most_common():
        lines.append(f"| `{type_marker}` | {count} |")
    lines.append("")

    lines.append("## Duplicate `message.id` groups (top 3)")
    lines.append("")
    groups = agg.duplicate_message_id_groups(limit=3)
    if not groups:
        lines.append("_No duplicates found — parallel-tool-use sharing not observed in this sample._")
    else:
        for g in groups:
            lines.append(f"- `{g['message_id']}` × {g['count']} — types: {g['type_markers']}")
    lines.append("")

    lines.append("## Redacted samples (one per category)")
    lines.append("")
    for category, sample in sorted(agg.samples.items()):
        lines.append(f"### `{category}`")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(sample, indent=2, default=str))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconnaissance script for Claude Code session JSONL schemas.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="One or more .jsonl session transcripts.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Write the markdown report to this file. Default: stdout.",
    )
    args = parser.parse_args(argv)

    agg = Aggregator()
    for path in args.files:
        if not path.exists():
            print(f"[skip] {path} does not exist", file=sys.stderr)
            continue
        for row in read_jsonl(path):
            agg.ingest(row)

    report = format_report(agg)
    if args.output is None:
        print(report)
    else:
        args.output.write_text(report, encoding="utf-8")
        print(f"Report written to {args.output} ({agg.row_count} rows ingested).",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
