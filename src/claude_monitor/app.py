"""
Claude Stream Monitor — Textual TUI application.
"""

from __future__ import annotations

import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from rich.text import Text

from claude_monitor.parser import (
    Event, EventKind, StreamParser,
    SessionStartEvent, CostUpdateEvent, PhaseChangeEvent,
    TicketChangeEvent, SessionEndEvent,
)
from claude_monitor.widgets.feed import FeedLog


class StatusBar(Static):
    """Top status bar: phase | ticket | session | elapsed."""

    phase_num: reactive[str] = reactive("—")
    phase_label: reactive[str] = reactive("Waiting for session…")
    ticket: reactive[str] = reactive("")
    session_id: reactive[str] = reactive("")
    elapsed: reactive[float] = reactive(0.0)
    cost: reactive[float] = reactive(0.0)
    input_tokens: reactive[int] = reactive(0)
    output_tokens: reactive[int] = reactive(0)

    DEFAULT_CSS = """
    StatusBar {
        height: 3;
        background: $surface;
        border-bottom: solid $primary;
        padding: 0 1;
        color: $text;
    }
    """

    def render(self) -> Text:
        t = Text()

        # Phase
        phase_color = _phase_color(self.phase_num)
        t.append(" Phase ", style="dim")
        t.append(self.phase_num, style=f"bold {phase_color}")
        t.append(f" {self.phase_label} ", style=phase_color)

        # Ticket
        if self.ticket:
            t.append("│ ", style="dim")
            t.append(self.ticket, style="bold yellow")
            t.append(" ", style="")

        # Spacer
        t.append("│ ", style="dim")

        # Session
        if self.session_id:
            short_id = self.session_id[:8]
            t.append(f"session:{short_id}… ", style="dim")

        # Elapsed
        elapsed = int(self.elapsed)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        t.append(f"{h:02d}:{m:02d}:{s:02d}", style="dim")

        # Cost + tokens
        t.append(" │ ", style="dim")
        t.append(f"${self.cost:.4f}", style="bold green")
        t.append(f"  ↑{self.input_tokens:,} ↓{self.output_tokens:,}", style="dim")

        return t


def _phase_color(phase: str) -> str:
    colors = {
        "0": "cyan", "1": "blue", "2": "yellow",
        "2.5": "magenta", "3": "green", "3a": "green",
        "4": "green", "5": "white", "6": "dim",
    }
    return colors.get(phase, "white")


class MonitorApp(App):
    """Claude Code stream-json monitor TUI."""

    TITLE = "Claude Stream Monitor"
    CSS = """
    Screen {
        background: $background;
    }
    #feed-container {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("a", "toggle_autoscroll", "Auto-scroll"),
        Binding("c", "clear_feed", "Clear"),
        Binding("p", "toggle_pause", "Pause"),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, log_path: Path | None = None, replay: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._parser = StreamParser()
        self._log_path = log_path
        self._log_file = None
        self._start_time = time.monotonic()
        self._paused = False
        self._autoscroll = True
        self._replay = replay

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(id="status")
        with Vertical(id="feed-container"):
            yield FeedLog(id="feed", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        if self._log_path:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self._log_path, "a", buffering=1)
        # Tick elapsed time every second
        self.set_interval(1.0, self._tick_elapsed)

    def on_unmount(self) -> None:
        if self._log_file:
            self._log_file.close()

    def _tick_elapsed(self) -> None:
        status = self.query_one("#status", StatusBar)
        status.elapsed = time.monotonic() - self._start_time

    def ingest_line(self, line: str) -> None:
        """Feed a raw JSON line into the parser and update the TUI."""
        # Always write to log file regardless of pause
        if self._log_file:
            self._log_file.write(line + "\n")

        events = self._parser.feed(line)
        if self._paused:
            return

        status = self.query_one("#status", StatusBar)
        feed = self.query_one("#feed", FeedLog)

        for event in events:
            self._apply_status(status, event)
            feed.push_event(event)

    def _apply_status(self, status: StatusBar, event: Event) -> None:
        if event.kind == EventKind.SESSION_START:
            ev: SessionStartEvent = event  # type: ignore
            status.session_id = ev.session_id
            self._start_time = time.monotonic()

        elif event.kind == EventKind.PHASE_CHANGE:
            ev: PhaseChangeEvent = event  # type: ignore
            status.phase_num = ev.phase_number
            status.phase_label = ev.phase_label

        elif event.kind == EventKind.TICKET_CHANGE:
            ev: TicketChangeEvent = event  # type: ignore
            status.ticket = ev.ticket_id

        elif event.kind == EventKind.COST_UPDATE:
            ev: CostUpdateEvent = event  # type: ignore
            status.cost = ev.total_cost_usd
            status.input_tokens = ev.input_tokens
            status.output_tokens = ev.output_tokens

        elif event.kind == EventKind.SESSION_END:
            ev: SessionEndEvent = event  # type: ignore
            status.cost = ev.total_cost_usd
            status.phase_label = "Complete"

    def action_toggle_autoscroll(self) -> None:
        feed = self.query_one("#feed", FeedLog)
        self._autoscroll = not self._autoscroll
        feed.auto_scroll = self._autoscroll
        self.notify(f"Auto-scroll {'on' if self._autoscroll else 'off'}")

    def action_clear_feed(self) -> None:
        self.query_one("#feed", FeedLog).clear()

    def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        self.notify(f"{'Paused' if self._paused else 'Resumed'} — log continues writing")
