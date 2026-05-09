"""
Plain-text fallback renderer — no Textual, just formatted stdout.
Used when the TUI crashes or for CI/non-interactive environments.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console
from rich.text import Text

from claude_monitor.parser import (
    StreamParser, EventKind,
    TextEvent, ToolStartEvent, ToolResultEvent,
    PhaseChangeEvent, TicketChangeEvent, CostUpdateEvent,
    SessionStartEvent, SessionEndEvent,
)

TOOL_COLORS = {
    "Bash": "cyan", "Read": "blue", "Write": "yellow", "Edit": "yellow",
    "Grep": "magenta", "Glob": "magenta", "Agent": "green",
}

MAX_RESULT_LINES = 50


def run_plain(log_path: Path | None) -> None:
    console = Console(stderr=False)
    parser = StreamParser()
    log_file = None

    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", buffering=1)

    try:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line:
                continue
            if log_file:
                log_file.write(line + "\n")
            try:
                events = parser.feed(line)
            except Exception as e:
                console.print(f"[red]⚠ parse error: {e}[/red]")
                continue

            for event in events:
                try:
                    _render(console, event)
                except Exception as e:
                    console.print(f"[red]⚠ render error [{event.kind}]: {e}[/red]")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        if log_file:
            log_file.close()


def _render(console: Console, event) -> None:
    if event.kind == EventKind.SESSION_START:
        ev: SessionStartEvent = event  # type: ignore
        console.rule(f"[bold]Session {ev.session_id[:8]}… — {ev.model}[/bold]")

    elif event.kind == EventKind.PHASE_CHANGE:
        ev: PhaseChangeEvent = event  # type: ignore
        console.print(f"\n[bold cyan]▶ Phase {ev.phase_number}: {ev.phase_label}[/bold cyan]")

    elif event.kind == EventKind.TICKET_CHANGE:
        ev: TicketChangeEvent = event  # type: ignore
        console.print(f"[bold yellow]  Ticket: {ev.ticket_id}[/bold yellow]")

    elif event.kind == EventKind.TOOL_START:
        ev: ToolStartEvent = event  # type: ignore
        color = TOOL_COLORS.get(ev.name, "white")
        t = Text()
        t.append("⏺ ", style=f"bold {color}")
        t.append(ev.name, style=f"bold {color}")
        inp = ev.input
        if ev.name == "Bash" and "command" in inp:
            cmd = inp["command"]
            t.append(f"({cmd[:100]}{'…' if len(cmd) > 100 else ''})", style="dim")
        elif "file" in inp:
            t.append(f"({inp['file']})", style="dim")
        elif "pattern" in inp:
            t.append(f"({inp['pattern']})", style="dim")
        console.print(t)

    elif event.kind == EventKind.TOOL_RESULT:
        ev: ToolResultEvent = event  # type: ignore
        if not ev.content.strip():
            return
        lines = ev.content.splitlines()
        truncated = len(lines) > MAX_RESULT_LINES
        display = "\n".join(lines[:MAX_RESULT_LINES])
        if truncated:
            display += f"\n… ({len(lines) - MAX_RESULT_LINES} more lines)"
        style = "red dim" if ev.is_error else "dim"
        prefix = "  ✗ " if ev.is_error else "  ↳ "
        console.print(f"{prefix}{display}", style=style)

    elif event.kind == EventKind.TEXT:
        ev: TextEvent = event  # type: ignore
        text = ev.text.strip()
        if text:
            console.print(f"\n[white]{text}[/white]")

    elif event.kind == EventKind.COST_UPDATE:
        ev: CostUpdateEvent = event  # type: ignore
        console.print(
            f"[dim]  cost: ${ev.total_cost_usd:.4f}  "
            f"↑{ev.input_tokens:,} ↓{ev.output_tokens:,}[/dim]"
        )

    elif event.kind == EventKind.SESSION_END:
        ev: SessionEndEvent = event  # type: ignore
        console.rule(f"[bold green]Session complete — ${ev.total_cost_usd:.4f}[/bold green]")
