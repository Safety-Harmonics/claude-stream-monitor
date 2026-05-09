"""
Claude Stream Monitor — Textual TUI application.
"""

from __future__ import annotations

import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static, RichLog
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

    DEFAULT_CSS = """
    StatusBar {
        height: 2;
        background: $panel;
        border-bottom: solid $primary;
        padding: 0 1;
        content-align: left middle;
    }
    """

    def __init__(self, **kwargs):
        super().__init__("Waiting for session…", **kwargs)
        self._phase_num     = "—"
        self._phase_label   = "Waiting for session…"
        self._ticket        = ""
        self._session_id    = ""
        self._elapsed       = 0.0
        self._cost          = 0.0
        self._input_tokens  = 0
        self._output_tokens = 0

    def update_state(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, f"_{k}", v)
        self._refresh_display()

    def _refresh_display(self) -> None:
        color = PHASE_COLORS.get(self._phase_num, "white")
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(" ◆ ", style=f"bold {color}")
        t.append(f"Phase {self._phase_num}", style=f"bold {color}")
        t.append(f"  {self._phase_label}", style=color)
        if self._ticket:
            t.append("   │   ", style="dim")
            t.append(self._ticket, style="bold yellow")
        if self._session_id:
            t.append("   │   ", style="dim")
            t.append(f"session:{self._session_id[:8]}…", style="dim")
        t.append("   │   ", style="dim")
        elapsed = int(self._elapsed)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        t.append(f"{h:02d}:{m:02d}:{s:02d}", style="dim")
        t.append("   │   ", style="dim")
        t.append(f"${self._cost:.4f}", style="bold green")
        t.append(f"  ↑{self._input_tokens:,} ↓{self._output_tokens:,}", style="dim")
        self.update(t)


class MonitorApp(App):
    """Claude Code stream-json monitor TUI."""

    TITLE = "Claude Stream Monitor"
    CSS = """
    Screen {
        background: $background;
        layers: base;
    }
    #feed {
        height: 1fr;
        border: none;
        padding: 0 1;
        scrollbar-size: 1 1;
    }
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
        self._widgets_mounted = False          # set True after on_mount completes
        self._pending_lines: list[str] = []  # lines received before ready

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(id="status")
        yield FeedLog(id="feed")
        yield Footer()

    def on_mount(self) -> None:
        if self._log_path:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self._log_path, "a", buffering=1)
        self.set_interval(1.0, self._tick_elapsed)
        self._widgets_mounted = True
        # Drain anything that arrived before we were ready
        for line in self._pending_lines:
            self._process_line(line)
        self._pending_lines.clear()

    def on_unmount(self) -> None:
        if self._log_file:
            self._log_file.close()

    def _tick_elapsed(self) -> None:
        status = self.query_one("#status", StatusBar)
        status._elapsed = time.monotonic() - self._start_time
        status._refresh_display()

    def ingest_line(self, line: str) -> None:
        if self._log_file:
            self._log_file.write(line + "\n")
        if not self._widgets_mounted:
            self._pending_lines.append(line)
            return
        self._process_line(line)

    def _process_line(self, line: str) -> None:
        try:
            events = self._parser.feed(line)
        except Exception as e:
            self._write_error(f"Parser error: {e} | line: {line[:120]}")
            return

        if self._paused:
            return

        try:
            status = self.query_one("#status", StatusBar)
            feed = self.query_one("#feed", FeedLog)
        except Exception as e:
            self._write_error(f"Widget query failed: {e}")
            return

        for event in events:
            try:
                self._apply_status(status, event)
                feed.push_event(event)
            except Exception as e:
                self._write_error(f"Render error [{event.kind}]: {e}")

    def _write_error(self, msg: str) -> None:
        try:
            feed = self.query_one("#feed", FeedLog)
            feed.write(Text(f"⚠ {msg}", style="bold red"))
        except Exception:
            pass  # If even this fails, silently ignore

    def _apply_status(self, status: StatusBar, event: Event) -> None:
        if event.kind == EventKind.SESSION_START:
            ev: SessionStartEvent = event  # type: ignore
            status.update_state(session_id=ev.session_id)
            self._start_time = time.monotonic()
        elif event.kind == EventKind.PHASE_CHANGE:
            ev: PhaseChangeEvent = event  # type: ignore
            status.update_state(phase_num=ev.phase_number, phase_label=ev.phase_label)
        elif event.kind == EventKind.TICKET_CHANGE:
            ev: TicketChangeEvent = event  # type: ignore
            status.update_state(ticket=ev.ticket_id)
        elif event.kind == EventKind.COST_UPDATE:
            ev: CostUpdateEvent = event  # type: ignore
            status.update_state(
                cost=ev.total_cost_usd,
                input_tokens=ev.input_tokens,
                output_tokens=ev.output_tokens,
            )
        elif event.kind == EventKind.SESSION_END:
            ev: SessionEndEvent = event  # type: ignore
            status.update_state(cost=ev.total_cost_usd, phase_label="Complete")

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
