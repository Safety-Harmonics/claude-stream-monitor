"""
Claude Stream Monitor — Textual TUI application.
"""

from __future__ import annotations

import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static
from textual.containers import Vertical
from textual.reactive import reactive
from rich.text import Text

from claude_monitor.parser import (
    Event, EventKind, StreamParser,
    SessionStartEvent, CostUpdateEvent, PhaseChangeEvent,
    TicketChangeEvent, SessionEndEvent,
)
from claude_monitor.widgets.feed import FeedLog


PHASE_COLORS = {
    "0": "cyan", "1": "blue", "2": "yellow", "2.5": "magenta",
    "3": "green", "3a": "green", "3b": "green", "3c": "green",
    "4": "bright_green", "5": "white", "6": "dim",
}


class StatusBar(Static):
    """Top status bar: phase | ticket | session | elapsed | cost."""

    phase_num:     reactive[str]   = reactive("—")
    phase_label:   reactive[str]   = reactive("Waiting for session…")
    ticket:        reactive[str]   = reactive("")
    session_id:    reactive[str]   = reactive("")
    elapsed:       reactive[float] = reactive(0.0)
    cost:          reactive[float] = reactive(0.0)
    input_tokens:  reactive[int]   = reactive(0)
    output_tokens: reactive[int]   = reactive(0)

    DEFAULT_CSS = """
    StatusBar {
        height: 2;
        background: $panel;
        border-bottom: solid $primary;
        padding: 0 1;
        content-align: left middle;
    }
    """

    def render(self) -> Text:
        color = PHASE_COLORS.get(self.phase_num, "white")
        t = Text(no_wrap=True, overflow="ellipsis")

        t.append(" ◆ ", style=f"bold {color}")
        t.append(f"Phase {self.phase_num}", style=f"bold {color}")
        t.append(f"  {self.phase_label}", style=color)

        if self.ticket:
            t.append("   │   ", style="dim")
            t.append(self.ticket, style="bold yellow")

        if self.session_id:
            t.append("   │   ", style="dim")
            t.append(f"session:{self.session_id[:8]}…", style="dim")

        t.append("   │   ", style="dim")
        elapsed = int(self.elapsed)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        t.append(f"{h:02d}:{m:02d}:{s:02d}", style="dim")

        t.append("   │   ", style="dim")
        t.append(f"${self.cost:.4f}", style="bold green")
        t.append(f"  ↑{self.input_tokens:,} ↓{self.output_tokens:,}", style="dim")

        return t

    # Watch methods trigger re-render when any reactive changes
    def watch_phase_num(self, _: str)     -> None: self.refresh()
    def watch_phase_label(self, _: str)   -> None: self.refresh()
    def watch_ticket(self, _: str)        -> None: self.refresh()
    def watch_session_id(self, _: str)    -> None: self.refresh()
    def watch_elapsed(self, _: float)     -> None: self.refresh()
    def watch_cost(self, _: float)        -> None: self.refresh()
    def watch_input_tokens(self, _: int)  -> None: self.refresh()
    def watch_output_tokens(self, _: int) -> None: self.refresh()


class MonitorApp(App):
    """Claude Code stream-json monitor TUI."""

    TITLE = "Claude Stream Monitor"
    CSS = """
    Screen { background: $background; }
    #feed-container { height: 1fr; }
    """

    BINDINGS = [
        Binding("q", "quit",              "Quit"),
        Binding("a", "toggle_autoscroll", "Auto-scroll"),
        Binding("c", "clear_feed",        "Clear"),
        Binding("p", "toggle_pause",      "Pause"),
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
        self.set_interval(1.0, self._tick_elapsed)

    def on_unmount(self) -> None:
        if self._log_file:
            self._log_file.close()

    def _tick_elapsed(self) -> None:
        self.query_one("#status", StatusBar).elapsed = time.monotonic() - self._start_time

    def ingest_line(self, line: str) -> None:
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
            status.phase_num   = ev.phase_number
            status.phase_label = ev.phase_label
        elif event.kind == EventKind.TICKET_CHANGE:
            ev: TicketChangeEvent = event  # type: ignore
            status.ticket = ev.ticket_id
        elif event.kind == EventKind.COST_UPDATE:
            ev: CostUpdateEvent = event  # type: ignore
            status.cost          = ev.total_cost_usd
            status.input_tokens  = ev.input_tokens
            status.output_tokens = ev.output_tokens
        elif event.kind == EventKind.SESSION_END:
            ev: SessionEndEvent = event  # type: ignore
            status.cost        = ev.total_cost_usd
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
        self.notify(f"{'Paused — log still writing' if self._paused else 'Resumed'}")
