"""
CLI entry point.

Usage:
    docker logs -f <container> | claude-monitor
    docker logs -f <container> | claude-monitor --log-dir logs/
    cat logs/agent-20260509.jsonl | claude-monitor --replay
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

import click

from claude_monitor.app import MonitorApp


def _stream_stdin(app: MonitorApp) -> None:
    """Read stdin line by line and feed into the app (runs in background thread)."""
    try:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if line:
                try:
                    app.call_from_thread(app.ingest_line, line)
                except RuntimeError:
                    break  # App stopped — exit cleanly
    except (KeyboardInterrupt, EOFError):
        pass


@click.command()
@click.option(
    "--log-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory to write raw JSONL logs (default: ./logs)",
)
@click.option(
    "--log-file",
    type=click.Path(path_type=Path),
    default=None,
    help="Explicit log file path (overrides --log-dir)",
)
@click.option(
    "--replay",
    is_flag=True,
    default=False,
    help="Replay mode: read from file at full speed without live timing",
)
@click.option(
    "--no-log",
    is_flag=True,
    default=False,
    help="Disable log file writing",
)
def main(
    log_dir: Path | None,
    log_file: Path | None,
    replay: bool,
    no_log: bool,
) -> None:
    """
    TUI monitor for Claude Code --output-format stream-json output.

    Pipe claude stream-json output into this tool:

        docker logs -f <container> | claude-monitor

        claude --output-format stream-json -p "..." | claude-monitor

    Replay a saved log:

        cat logs/agent-20260509.jsonl | claude-monitor --replay
    """
    resolved_log: Path | None = None
    if not no_log:
        if log_file:
            resolved_log = log_file
        else:
            base = log_dir or Path("logs")
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            resolved_log = base / f"agent-{timestamp}.jsonl"

    app = MonitorApp(log_path=resolved_log, replay=replay)

    t = threading.Thread(target=_stream_stdin, args=(app,), daemon=True)
    t.start()

    app.run()


if __name__ == "__main__":
    main()
