"""Tokenifier CLI entrypoint.

`uv run tokenifier` reads the most recent Claude Code session JSONL for
the current working directory and renders it as a per-turn context-budget
chart. Pass an explicit path to render a specific session.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from tokenifier.parser import parse_session
from tokenifier.render import render


app = typer.Typer(add_completion=False, help="Visualise per-turn context-budget usage.")


CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


def _encode_cwd_for_projects(cwd: Path) -> str:
    """Convert /Users/icheng/greenlit → -Users-icheng-greenlit.

    Claude Code stores per-project session JSONLs under directories whose
    names are the absolute project path with `/` replaced by `-`.
    """
    return str(cwd).replace("/", "-")


def _find_latest_session(cwd: Path) -> Optional[Path]:
    """Return the most recently modified `.jsonl` under the project dir for `cwd`, or None."""
    project_dir = CLAUDE_PROJECTS / _encode_cwd_for_projects(cwd)
    if not project_dir.is_dir():
        return None
    jsonls = list(project_dir.glob("*.jsonl"))
    if not jsonls:
        return None
    return max(jsonls, key=lambda p: p.stat().st_mtime)


@app.callback(invoke_without_command=True)
def main(
    file: Optional[Path] = typer.Argument(
        None,
        help="Path to a session JSONL. Default: latest session for current directory.",
    ),
    latest: bool = typer.Option(
        False,
        "--latest",
        help="Explicit alias for the default behaviour: most recent session for cwd.",
    ),
) -> None:
    """Render the per-turn context budget for one Claude Code session."""
    if file is not None:
        if not file.is_file():
            typer.echo(f"File not found: {file}", err=True)
            raise typer.Exit(1)
        path = file
    else:
        cwd = Path.cwd()
        path = _find_latest_session(cwd)
        if path is None:
            encoded = _encode_cwd_for_projects(cwd)
            typer.echo(
                f"No JSONL sessions found in {CLAUDE_PROJECTS / encoded}",
                err=True,
            )
            typer.echo(
                "Try: tokenifier <path/to/session.jsonl>",
                err=True,
            )
            raise typer.Exit(1)

    typer.echo(f"Reading {path}", err=True)
    talks = parse_session(path)

    if not talks:
        typer.echo("No talks to render (no assistant turns after filtering).", err=True)
        raise typer.Exit(1)

    total_turns = sum(t.turn_count for t in talks)
    typer.echo(
        f"  {len(talks)} talk(s) / {total_turns} assistant turn(s) after filtering",
        err=True,
    )
    render(talks)


if __name__ == "__main__":
    app()
