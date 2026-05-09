"""
Tool call feed widget — scrollable log of tool calls and Claude text.
"""

from __future__ import annotations

from textual.widgets import RichLog
from rich.text import Text
from rich.markdown import Markdown

from claude_monitor.parser import (
    Event, EventKind, TextEvent, ToolStartEvent, ToolResultEvent, SessionEndEvent,
)

TOOL_COLORS = {
    "Bash":      "bold cyan",
    "Read":      "bold blue",
    "Write":     "bold yellow",
    "Edit":      "bold yellow",
    "Grep":      "bold magenta",
    "Glob":      "bold magenta",
    "Agent":     "bold green",
    "WebFetch":  "bold blue",
    "WebSearch": "bold blue",
}

MAX_RESULT_LINES = 20


class FeedLog(RichLog):
    """Scrollable feed of tool calls and Claude responses."""

    DEFAULT_CSS = """
    FeedLog {
        height: 1fr;
        border: none;
        padding: 0 1;
        scrollbar-size: 1 1;
    }
    """

    def push_event(self, event: Event) -> None:
        if event.kind == EventKind.TOOL_START:
            self._render_tool_start(event)  # type: ignore
        elif event.kind == EventKind.TOOL_RESULT:
            self._render_tool_result(event)  # type: ignore
        elif event.kind == EventKind.TEXT:
            self._render_text(event)  # type: ignore
        elif event.kind == EventKind.SESSION_END:
            self._render_session_end(event)  # type: ignore

    def _render_tool_start(self, ev: ToolStartEvent) -> None:
        color = TOOL_COLORS.get(ev.name, "bold white")
        label = Text()
        label.append("⏺ ", style=color)
        label.append(ev.name, style=color)

        inp = ev.input
        if ev.name == "Bash" and "command" in inp:
            cmd = inp["command"]
            display = cmd[:120] + "…" if len(cmd) > 120 else cmd
            label.append(f"({display})", style="dim")
        elif ev.name in ("Read", "Write", "Edit") and "file" in inp:
            label.append(f"({inp['file']})", style="dim")
        elif ev.name in ("Grep", "Glob") and "pattern" in inp:
            label.append(f"({inp['pattern']})", style="dim")

        self.write(label)

    def _render_tool_result(self, ev: ToolResultEvent) -> None:
        if not ev.content.strip():
            return

        lines = ev.content.splitlines()
        truncated = len(lines) > MAX_RESULT_LINES
        display = "\n".join(lines[:MAX_RESULT_LINES])
        if truncated:
            display += f"\n… ({len(lines) - MAX_RESULT_LINES} more lines)"

        result = Text()
        if ev.is_error:
            result.append("  ✗ ", style="bold red")
            result.append(display, style="red dim")
        else:
            result.append("  ↳ ", style="dim")
            result.append(display, style="dim")

        self.write(result)
        self.write("")

    def _render_text(self, ev: TextEvent) -> None:
        text = ev.text.strip()
        if not text:
            return
        self.write(Text(text, style="white"))
        self.write("")

    def _render_session_end(self, ev: SessionEndEvent) -> None:
        self.write("")
        self.write(Text("━" * 60, style="dim"))
        result_text = ev.result[:200] if ev.result else "Session complete"
        self.write(Text(f"Session ended: {result_text}", style="bold green"))
